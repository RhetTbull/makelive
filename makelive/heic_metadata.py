"""Byte-level HEIC Live-Photo ContentIdentifier injection.

Apple's MakerNote cannot be written by `CGImageDestinationCopyImageSource`
(it isn't representable in CGImageMetadata's XMP-style model), and
`CGImageDestinationAddImageFromSource` always re-encodes HEIC pixels. To
preserve the HEVC image bitstream byte-for-byte, this module does the
metadata surgery directly on the ISOBMFF box structure:

  1. Walk top-level boxes to find `meta` and its `iinf` / `iloc` children.
  2. Locate the item whose `item_type` is `'Exif'`.
  3. Read its EXIF blob, parse with piexif, inject an Apple-format MakerNote
     containing the ContentIdentifier UUID, re-serialise.
  4. Append the new EXIF blob into the trailing `mdat` box (extending the
     box's size header so the bytes stay enclosed — strict readers like
     Adobe Camera Raw reject files with data outside any box) and update
     the EXIF item's `iloc` extent to point at the new offset/length.
  5. Write the modified file in place. HEVC bitstream inside `mdat` is
     untouched; old EXIF bytes inside `mdat` become a dead gap.

Constraints assumed (validated for iPhone HEICs and the bundled test image):
  • A single `Exif` item exists in `iinf`.
  • Its `iloc` entry uses exactly one extent.
  • `iloc` construction_method is 0 (absolute file offset).
  • `mdat` is the last top-level box (true for iPhone / Android HEICs).
The injection raises NotImplementedError if any of these doesn't hold.
"""

from __future__ import annotations

import io
import os
import pathlib
import struct
from typing import NamedTuple

import piexif


# ---------- ISOBMFF box walker ----------


class Box(NamedTuple):
    type: str
    offset: int            # offset of box header in file
    header_size: int       # 8 or 16
    content_offset: int    # offset of box content
    content_size: int      # bytes of content (excludes header)

    @property
    def total_size(self) -> int:
        return self.header_size + self.content_size


def _read_box(data: bytes, offset: int, end: int) -> Box:
    if offset + 8 > end:
        raise ValueError(f"truncated box at offset {offset}")
    size = struct.unpack_from(">I", data, offset)[0]
    type_ = data[offset + 4 : offset + 8].decode("latin1")
    header_size = 8
    if size == 1:
        size = struct.unpack_from(">Q", data, offset + 8)[0]
        header_size = 16
    elif size == 0:
        size = end - offset
    return Box(type_, offset, header_size, offset + header_size, size - header_size)


def _walk_boxes(data: bytes, start: int, end: int):
    p = start
    while p < end:
        box = _read_box(data, p, end)
        yield box
        p = box.offset + box.total_size


def _find_box(data: bytes, start: int, end: int, type_: str) -> Box | None:
    for box in _walk_boxes(data, start, end):
        if box.type == type_:
            return box
    return None


# ---------- iinf: locate the EXIF item id ----------


def _find_exif_item_id(data: bytes, iinf: Box) -> int | None:
    p = iinf.content_offset
    version = data[p]
    p += 4  # skip version+flags
    if version == 0:
        count = struct.unpack_from(">H", data, p)[0]
        p += 2
    else:
        count = struct.unpack_from(">I", data, p)[0]
        p += 4
    end = iinf.content_offset + iinf.content_size
    for _ in range(count):
        if p >= end:
            break
        infe_size = struct.unpack_from(">I", data, p)[0]
        infe_end = p + infe_size
        infe_version = data[p + 8]
        q = p + 12  # past box header + version+flags
        if infe_version >= 2:
            if infe_version == 2:
                item_id = struct.unpack_from(">H", data, q)[0]
                q += 2
            else:
                item_id = struct.unpack_from(">I", data, q)[0]
                q += 4
            q += 2  # item_protection_index
            item_type = data[q : q + 4].decode("latin1", errors="replace")
            if item_type == "Exif":
                return item_id
        p = infe_end
    return None


# ---------- iloc: parse and serialise ----------


class _ILocHeader(NamedTuple):
    version: int
    flags: int
    offset_size: int
    length_size: int
    base_offset_size: int
    index_size: int


class _ILocEntry(NamedTuple):
    item_id: int
    construction_method: int
    data_reference_index: int
    base_offset: int
    extents: list  # list of (index, offset, length)


def _parse_iloc(data: bytes, iloc: Box) -> tuple[_ILocHeader, list[_ILocEntry]]:
    p = iloc.content_offset
    vf = struct.unpack_from(">I", data, p)[0]
    p += 4
    version = vf >> 24
    flags = vf & 0xFFFFFF
    b1, b2 = data[p], data[p + 1]
    p += 2
    offset_size = b1 >> 4
    length_size = b1 & 0x0F
    base_offset_size = b2 >> 4
    index_size = (b2 & 0x0F) if version >= 1 else 0
    if version < 2:
        item_count = struct.unpack_from(">H", data, p)[0]
        p += 2
    else:
        item_count = struct.unpack_from(">I", data, p)[0]
        p += 4

    entries: list[_ILocEntry] = []
    for _ in range(item_count):
        if version < 2:
            item_id = struct.unpack_from(">H", data, p)[0]
            p += 2
        else:
            item_id = struct.unpack_from(">I", data, p)[0]
            p += 4
        if version >= 1:
            cm = struct.unpack_from(">H", data, p)[0] & 0x0F
            p += 2
        else:
            cm = 0
        dri = struct.unpack_from(">H", data, p)[0]
        p += 2
        base = int.from_bytes(data[p : p + base_offset_size], "big") if base_offset_size else 0
        p += base_offset_size
        ext_count = struct.unpack_from(">H", data, p)[0]
        p += 2
        extents = []
        for _ in range(ext_count):
            if version >= 1 and index_size:
                idx = int.from_bytes(data[p : p + index_size], "big")
                p += index_size
            else:
                idx = 0
            off = int.from_bytes(data[p : p + offset_size], "big")
            p += offset_size
            length = int.from_bytes(data[p : p + length_size], "big")
            p += length_size
            extents.append((idx, off, length))
        entries.append(_ILocEntry(item_id, cm, dri, base, extents))
    return _ILocHeader(version, flags, offset_size, length_size, base_offset_size, index_size), entries


def _serialise_iloc_payload(header: _ILocHeader, entries: list[_ILocEntry]) -> bytes:
    buf = io.BytesIO()
    buf.write(struct.pack(">I", (header.version << 24) | (header.flags & 0xFFFFFF)))
    buf.write(
        bytes(
            [
                ((header.offset_size & 0xF) << 4) | (header.length_size & 0xF),
                ((header.base_offset_size & 0xF) << 4) | (header.index_size & 0xF),
            ]
        )
    )
    if header.version < 2:
        buf.write(struct.pack(">H", len(entries)))
    else:
        buf.write(struct.pack(">I", len(entries)))
    for e in entries:
        if header.version < 2:
            buf.write(struct.pack(">H", e.item_id))
        else:
            buf.write(struct.pack(">I", e.item_id))
        if header.version >= 1:
            buf.write(struct.pack(">H", e.construction_method & 0x0F))
        buf.write(struct.pack(">H", e.data_reference_index))
        buf.write(e.base_offset.to_bytes(header.base_offset_size, "big"))
        buf.write(struct.pack(">H", len(e.extents)))
        for idx, off, length in e.extents:
            if header.version >= 1 and header.index_size:
                buf.write(idx.to_bytes(header.index_size, "big"))
            buf.write(off.to_bytes(header.offset_size, "big"))
            buf.write(length.to_bytes(header.length_size, "big"))
    return buf.getvalue()


# ---------- Apple MakerNote constructor ----------


def _build_apple_makernote(asset_id: str) -> bytes:
    """Construct an Apple-format MakerNote containing only ContentIdentifier (tag 0x0011).

    Layout (offsets are relative to the MakerNote start, which is how Apple's
    reader interprets them):

        0-9     "Apple iOS\\x00"
        10-11   0x0001
        12-13   "MM"           (big-endian TIFF byte order)
        14-15   1              (entry count)
        16-27   IFD entry      tag=0x0011 (ContentIdentifier),
                               format=2 (ASCII),
                               count=len(uuid)+1,
                               value_offset=32
        28-31   0              (next-IFD offset)
        32+     UUID + '\\x00' (37 bytes for a 36-char canonical UUID)
    """
    value = asset_id.encode("ascii") + b"\x00"
    buf = io.BytesIO()
    buf.write(b"Apple iOS\x00")
    buf.write(b"\x00\x01")
    buf.write(b"MM")
    buf.write(struct.pack(">H", 1))
    buf.write(struct.pack(">H", 0x0011))   # tag: ContentIdentifier
    buf.write(struct.pack(">H", 2))        # format: ASCII
    buf.write(struct.pack(">I", len(value)))  # count
    buf.write(struct.pack(">I", 32))       # offset (MakerNote-relative)
    buf.write(struct.pack(">I", 0))        # next IFD offset
    buf.write(value)
    return buf.getvalue()


def _inject_makernote(exif_tiff_blob: bytes, asset_id: str) -> bytes:
    """Parse a TIFF/EXIF blob with piexif, set Apple MakerNote, return new blob."""
    exif_dict = piexif.load(exif_tiff_blob)
    exif_dict["Exif"][piexif.ExifIFD.MakerNote] = _build_apple_makernote(asset_id)
    return piexif.dump(exif_dict)


# ---------- Public entry point ----------


def set_heic_content_identifier(path: str | os.PathLike, asset_id: str) -> None:
    """Add/replace the Apple Live-Photo ContentIdentifier in a HEIC/HEIF file.

    Modifies `path` in place WITHOUT re-encoding the HEVC image data. Only
    the EXIF metadata item is rewritten and the iloc box updated.

    Raises NotImplementedError for files that violate the assumptions
    documented at the top of this module (multi-extent EXIF, no EXIF item,
    construction_method != 0).
    """
    path = pathlib.Path(path)
    data = bytearray(path.read_bytes())

    meta = _find_box(data, 0, len(data), "meta")
    if meta is None:
        raise ValueError(f"{path}: no 'meta' box")
    # Find mdat and require it to be the last top-level box. We append the
    # new EXIF blob *inside* it (by growing its size header) so we don't
    # leave bytes outside any box — strict ISOBMFF readers (Adobe Camera
    # Raw, etc.) reject files with trailing unenclosed data.
    last_box = None
    for b in _walk_boxes(data, 0, len(data)):
        last_box = b
    if last_box is None or last_box.type != "mdat":
        raise NotImplementedError(
            f"{path}: last top-level box is "
            f"{last_box.type if last_box else 'None'!r}, not 'mdat'; "
            f"appending into a non-trailing mdat is not supported."
        )
    mdat = last_box
    # `meta` is a FullBox: children start 4 bytes past the box header.
    children_start = meta.content_offset + 4
    children_end = meta.content_offset + meta.content_size

    iinf = _find_box(data, children_start, children_end, "iinf")
    iloc = _find_box(data, children_start, children_end, "iloc")
    if iinf is None or iloc is None:
        raise ValueError(f"{path}: missing iinf/iloc")

    exif_id = _find_exif_item_id(data, iinf)
    if exif_id is None:
        raise NotImplementedError(
            f"{path}: no EXIF item — creating one from scratch is not implemented."
        )

    header, entries = _parse_iloc(data, iloc)
    exif_entry = next((e for e in entries if e.item_id == exif_id), None)
    if exif_entry is None:
        raise ValueError(f"{path}: EXIF item {exif_id} not in iloc")
    if exif_entry.construction_method != 0:
        raise NotImplementedError(
            f"{path}: EXIF item uses construction_method "
            f"{exif_entry.construction_method}; only absolute file offsets supported."
        )
    if len(exif_entry.extents) != 1:
        raise NotImplementedError(
            f"{path}: EXIF item has {len(exif_entry.extents)} extents; only 1 supported."
        )

    # Read the existing EXIF item: 4-byte BE TIFF-header-offset, optional
    # padding, then the TIFF blob.
    _idx, off, length = exif_entry.extents[0]
    abs_off = exif_entry.base_offset + off
    exif_item = bytes(data[abs_off : abs_off + length])
    tiff_offset = struct.unpack(">I", exif_item[:4])[0]
    tiff_blob = exif_item[4 + tiff_offset :]

    # Inject Apple MakerNote. piexif.dump() always prepends "Exif\0\0" before
    # the TIFF header, so the item's TIFF-header-offset must be 6 to point at
    # the real "MM"/"II" magic past that prefix. (Matches what Apple writes.)
    new_tiff = _inject_makernote(tiff_blob, asset_id)
    assert new_tiff.startswith(b"Exif\x00\x00"), "piexif output should be Exif-prefixed"
    new_exif_item = struct.pack(">I", 6) + new_tiff

    # New extent: append to end of file, point iloc at it.
    new_extent_offset = len(data)
    new_extent_length = len(new_exif_item)

    # Sanity: the offset/length fields in iloc are fixed-width; make sure the
    # appended location still fits.
    max_offset = (1 << (header.offset_size * 8)) - 1
    max_length = (1 << (header.length_size * 8)) - 1
    if new_extent_offset > max_offset or new_extent_length > max_length:
        raise NotImplementedError(
            f"{path}: appended EXIF doesn't fit in iloc's "
            f"{header.offset_size}-byte offset / {header.length_size}-byte length fields."
        )

    new_entries = [
        e._replace(extents=[(0, new_extent_offset, new_extent_length)]) if e.item_id == exif_id else e
        for e in entries
    ]
    new_iloc_payload = _serialise_iloc_payload(header, new_entries)
    # iloc payload size must not change (we kept one extent → one extent).
    if len(new_iloc_payload) != iloc.content_size:
        raise NotImplementedError(
            f"{path}: iloc payload size changed "
            f"({iloc.content_size} → {len(new_iloc_payload)}); resizing not implemented."
        )

    # Overwrite iloc payload in place; extend mdat to swallow the appended
    # EXIF blob so no bytes sit outside any box.
    data[iloc.content_offset : iloc.content_offset + iloc.content_size] = new_iloc_payload
    data.extend(new_exif_item)
    new_mdat_total = mdat.total_size + len(new_exif_item)
    if mdat.header_size == 8:
        # Compact 32-bit size header at bytes [mdat.offset .. mdat.offset+4).
        if new_mdat_total > 0xFFFFFFFF:
            raise NotImplementedError(
                f"{path}: extended mdat exceeds 4 GiB; promoting the size "
                f"header to 64-bit would require shifting subsequent bytes."
            )
        struct.pack_into(">I", data, mdat.offset, new_mdat_total)
    else:
        # 64-bit extended size at bytes [mdat.offset+8 .. mdat.offset+16).
        struct.pack_into(">Q", data, mdat.offset + 8, new_mdat_total)
    path.write_bytes(bytes(data))
