"""Byte-level MP4 / QuickTime ContentIdentifier injection.

Parallel to `heic_metadata`: inject the Apple Live Photo
`com.apple.quicktime.content.identifier` value into the `moov` box of an
MP4 / MOV file without re-encoding any track data.

Strategy
--------
  1. Parse top-level boxes; locate `moov`.
  2. Locate `moov/meta` (if any).
  3. Add or replace the `com.apple.quicktime.content.identifier` key/value
     inside `meta`, preserving any other keys/values present.
  4. Re-serialise `moov`. If the new `moov` is the same size as the original
     it is written in place. Otherwise the original `moov` region is
     overwritten with a `free` box of identical size (so file offsets that
     point into `mdat` stay valid — chunk offsets inside `stco`/`co64`
     reference absolute file positions in `mdat`, which never moves) and
     the new `moov` is appended at the end of the file.

The HEVC bitstream inside `mdat` is byte-identical to the source.

Caveats
-------
  * `moov/meta` has *two* incompatible representations: ISO/IEC 14496-12
    treats it as a `FullBox` (4-byte version+flags before children); Apple
    QuickTime defines it as a plain `Box` (no version+flags). Both forms
    are common in the wild — Android-sourced `.mp4` files are usually
    FullBox, iPhone-sourced `.mov` files (and many `.mp4` files with the
    `qt  ` major brand) are not. This module sniffs the existing form when
    a `meta` is present and matches it; when creating one from scratch it
    keys off the `ftyp` major brand.
  * Chunk-offset boxes (`stco` / `co64`) remain correct because `mdat` is
    not moved and the new `moov`, wherever it ends up, references the same
    absolute file positions.
"""

from __future__ import annotations

import io
import os
import pathlib
import struct
from typing import NamedTuple

CONTENT_IDENTIFIER_KEY = b"com.apple.quicktime.content.identifier"
MDTA_NAMESPACE = b"mdta"
TYPE_INDICATOR_UTF8 = 1


# ---------- ISOBMFF box primitives ----------


class Box(NamedTuple):
    type: bytes            # 4-byte box type
    offset: int            # offset of box header in the file
    header_size: int       # 8 or 16
    content_offset: int    # offset of box content
    content_size: int      # bytes of content (excludes header)

    @property
    def total_size(self) -> int:
        return self.header_size + self.content_size

    @property
    def end(self) -> int:
        return self.offset + self.total_size


def _read_box(data, offset: int, end: int) -> Box:
    if offset + 8 > end:
        raise ValueError(f"truncated box at offset {offset}")
    size = struct.unpack_from(">I", data, offset)[0]
    type_ = bytes(data[offset + 4 : offset + 8])
    header = 8
    if size == 1:
        size = struct.unpack_from(">Q", data, offset + 8)[0]
        header = 16
    elif size == 0:
        size = end - offset
    return Box(type_, offset, header, offset + header, size - header)


def _walk(data, start: int, end: int):
    p = start
    while p < end:
        box = _read_box(data, p, end)
        yield box
        p = box.end


def _find(data, start: int, end: int, type_: bytes) -> Box | None:
    for box in _walk(data, start, end):
        if box.type == type_:
            return box
    return None


def _box(type_: bytes, content: bytes) -> bytes:
    """Build a plain Box with a 32-bit size header. type_ must be exactly 4 bytes."""
    total = 8 + len(content)
    if total > 0xFFFFFFFF:
        raise NotImplementedError("64-bit box size headers not implemented")
    return struct.pack(">I", total) + type_ + content


def _fullbox(type_: bytes, content: bytes, version: int = 0, flags: int = 0) -> bytes:
    """Build a FullBox (Box with a 4-byte version+flags prefix on its content)."""
    vf = (version << 24) | (flags & 0xFFFFFF)
    return _box(type_, struct.pack(">I", vf) + content)


# ---------- meta-box style detection ----------


def _meta_is_fullbox(data, meta: Box) -> bool:
    """QuickTime `meta` is a plain Box; ISO/IEC 14496-12 `meta` is a FullBox.

    Discriminate by trying the FullBox-not-FullBox split: if the first 8 bytes
    of meta's content parse as a valid box header (plausible size, ASCII type),
    treat as a plain Box. Otherwise treat as a FullBox.
    """
    if meta.content_size < 12:
        return True  # too small to differentiate; assume FullBox
    # FullBox interpretation: skip 4 bytes (version+flags), then a box header.
    # Plain-box interpretation: a box header starts immediately.
    plain_size = struct.unpack_from(">I", data, meta.content_offset)[0]
    plain_type = bytes(data[meta.content_offset + 4 : meta.content_offset + 8])
    plain_ok = (
        8 <= plain_size <= meta.content_size
        and all(32 <= b < 127 for b in plain_type)
    )
    return not plain_ok


def _ftyp_is_quicktime(data) -> bool:
    """Return True if the file's ftyp brands indicate QuickTime (Apple) flavour."""
    ftyp = _find(data, 0, len(data), b"ftyp")
    if ftyp is None:
        return False
    if ftyp.content_size < 4:
        return False
    major = bytes(data[ftyp.content_offset : ftyp.content_offset + 4])
    # Brands beyond major + minor_version are the compatible brands list.
    compat = bytes(data[ftyp.content_offset + 8 : ftyp.content_offset + ftyp.content_size])
    return major == b"qt  " or b"qt  " in compat


# ---------- key index lookup ----------


def _parse_keys(data, keys_box: Box) -> list[tuple[bytes, bytes]]:
    """Return list of (namespace, key_value) for entries in a `keys` box (FullBox)."""
    p = keys_box.content_offset + 4  # skip version+flags
    end = keys_box.content_offset + keys_box.content_size
    if p + 4 > end:
        return []
    entry_count = struct.unpack_from(">I", data, p)[0]
    p += 4
    entries = []
    for _ in range(entry_count):
        if p + 8 > end:
            break
        key_size = struct.unpack_from(">I", data, p)[0]
        namespace = bytes(data[p + 4 : p + 8])
        value = bytes(data[p + 8 : p + key_size])
        entries.append((namespace, value))
        p += key_size
    return entries


# ---------- meta construction ----------


def _build_hdlr() -> bytes:
    """Apple-style metadata handler reference box."""
    # hdlr is always a FullBox.
    return _fullbox(
        b"hdlr",
        struct.pack(">I", 0)  # pre_defined
        + b"mdta"             # handler_type
        + b"\x00" * 12        # reserved[3]
        + b"\x00",            # name (empty null-terminated)
    )


def _build_keys_box(entries: list[tuple[bytes, bytes]]) -> bytes:
    """Build a `keys` FullBox from an ordered list of (namespace, key_value)."""
    body = struct.pack(">I", len(entries))
    for namespace, key_value in entries:
        key_size = 4 + 4 + len(key_value)  # size + namespace + value
        body += struct.pack(">I", key_size) + namespace + key_value
    return _fullbox(b"keys", body)


def _build_ilst_data(value: bytes, type_indicator: int = TYPE_INDICATOR_UTF8) -> bytes:
    """Build a `data` FullBox carrying a single value.

    The FullBox version/flags field doubles as Apple's type-indicator
    (version=0, flags=type_indicator). The first 4 content bytes after the
    version+flags are the locale (set to 0).
    """
    return _fullbox(b"data", struct.pack(">I", 0) + value, flags=type_indicator)


def _build_ilst_box(items: list[tuple[int, bytes]]) -> bytes:
    """Build an `ilst` Box from an ordered list of (key_index, data_value_bytes).

    Each `ilst` child's box-type is the 1-based key index packed big-endian.
    """
    body = b""
    for key_index, value in items:
        type_field = struct.pack(">I", key_index)
        body += _box(type_field, _build_ilst_data(value))
    return _box(b"ilst", body)


def _build_meta_box(asset_id: str, *, as_fullbox: bool) -> bytes:
    """Build a fresh `moov/meta` containing only the Apple ContentIdentifier."""
    hdlr = _build_hdlr()
    keys = _build_keys_box([(MDTA_NAMESPACE, CONTENT_IDENTIFIER_KEY)])
    ilst = _build_ilst_box([(1, asset_id.encode("ascii"))])
    content = hdlr + keys + ilst
    if as_fullbox:
        return _fullbox(b"meta", content)
    return _box(b"meta", content)


def _build_replacement_meta(
    data, meta_box: Box, asset_id: str
) -> bytes:
    """Build a replacement `meta` box that preserves existing keys/values
    and adds (or overwrites) the content identifier."""
    is_fullbox = _meta_is_fullbox(data, meta_box)
    content_start = meta_box.content_offset + (4 if is_fullbox else 0)
    content_end = meta_box.content_offset + meta_box.content_size

    hdlr_box: Box | None = None
    keys_box: Box | None = None
    ilst_box: Box | None = None
    other_children: list[Box] = []

    for child in _walk(data, content_start, content_end):
        if child.type == b"hdlr":
            hdlr_box = child
        elif child.type == b"keys":
            keys_box = child
        elif child.type == b"ilst":
            ilst_box = child
        else:
            other_children.append(child)

    # Pull existing keys + ilst items so we can preserve them.
    existing_keys: list[tuple[bytes, bytes]] = (
        _parse_keys(data, keys_box) if keys_box is not None else []
    )

    # Pull existing ilst items (as full bytes — we don't need to reinterpret).
    existing_ilst_items: dict[int, bytes] = {}  # key_index -> raw bytes of `data` child
    if ilst_box is not None:
        for item in _walk(data, ilst_box.content_offset, ilst_box.content_offset + ilst_box.content_size):
            key_index = struct.unpack(">I", item.type)[0]
            # Capture the FIRST data child's full bytes.
            for sub in _walk(data, item.content_offset, item.content_offset + item.content_size):
                if sub.type == b"data":
                    existing_ilst_items[key_index] = bytes(data[sub.offset : sub.end])
                    break

    # Find or assign an index for the content-identifier key.
    target_index = None
    for i, (ns, kv) in enumerate(existing_keys, start=1):
        if ns == MDTA_NAMESPACE and kv == CONTENT_IDENTIFIER_KEY:
            target_index = i
            break
    if target_index is None:
        existing_keys.append((MDTA_NAMESPACE, CONTENT_IDENTIFIER_KEY))
        target_index = len(existing_keys)

    # Build the new hdlr (use existing if present, else our standard mdta one).
    hdlr_bytes = (
        bytes(data[hdlr_box.offset : hdlr_box.end]) if hdlr_box is not None else _build_hdlr()
    )

    # Build the new keys box from the (possibly extended) entries.
    keys_bytes = _build_keys_box(existing_keys)

    # Build the new ilst: every existing item except the content identifier,
    # plus our content identifier (overwriting any previous).
    new_items_blob = b""
    for key_index, data_box_bytes in existing_ilst_items.items():
        if key_index == target_index:
            continue
        new_items_blob += _box(struct.pack(">I", key_index), data_box_bytes)
    # Append our content identifier item.
    new_items_blob += _box(
        struct.pack(">I", target_index),
        _build_ilst_data(asset_id.encode("ascii")),
    )
    ilst_bytes = _box(b"ilst", new_items_blob)

    # Preserve any boxes we didn't recognise.
    others_blob = b"".join(
        bytes(data[child.offset : child.end]) for child in other_children
    )

    content = hdlr_bytes + keys_bytes + ilst_bytes + others_blob
    if is_fullbox:
        return _fullbox(b"meta", content)
    return _box(b"meta", content)


# ---------- moov rewrite ----------


def _build_new_moov(data, moov: Box, asset_id: str) -> bytes:
    """Return the bytes of a new `moov` box with the content identifier set.

    Walks moov's children, replacing (or appending) the `meta` subtree.
    """
    meta_box = _find(data, moov.content_offset, moov.content_offset + moov.content_size, b"meta")
    if meta_box is not None:
        new_meta = _build_replacement_meta(data, meta_box, asset_id)
        new_moov_content = (
            bytes(data[moov.content_offset : meta_box.offset])
            + new_meta
            + bytes(data[meta_box.end : moov.content_offset + moov.content_size])
        )
    else:
        new_meta = _build_meta_box(asset_id, as_fullbox=not _ftyp_is_quicktime(data))
        new_moov_content = (
            bytes(data[moov.content_offset : moov.content_offset + moov.content_size])
            + new_meta
        )
    return _box(b"moov", new_moov_content)


# ---------- public entry point ----------


def set_mp4_content_identifier(path: str | os.PathLike, asset_id: str) -> None:
    """Add or replace the Apple Live-Photo ContentIdentifier in an MP4 / MOV file.

    Modifies `path` in place WITHOUT re-encoding any track data. Only the
    `moov` box is rewritten; `mdat` and HEVC bitstream are byte-identical
    to the source.
    """
    path = pathlib.Path(path)
    data = bytearray(path.read_bytes())

    moov = _find(data, 0, len(data), b"moov")
    if moov is None:
        raise ValueError(f"{path}: no 'moov' box")

    new_moov_bytes = _build_new_moov(data, moov, asset_id)

    if len(new_moov_bytes) == moov.total_size:
        # Same size — write in place.
        data[moov.offset : moov.end] = new_moov_bytes
        path.write_bytes(bytes(data))
        return

    # Different size — replace original moov region with a `free` box of
    # identical size (keeps file offsets stable so chunk-offset tables stay
    # valid) and append the new moov at the end of the file.
    free_size = moov.total_size
    if free_size < 8:
        raise ValueError(f"{path}: original moov is too small to be replaced by a free box")
    free_box = struct.pack(">I", free_size) + b"free" + b"\x00" * (free_size - 8)

    data[moov.offset : moov.end] = free_box
    data.extend(new_moov_bytes)
    path.write_bytes(bytes(data))
