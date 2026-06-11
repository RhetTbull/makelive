"""Test makelive.py"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import uuid
from functools import cache
from typing import Any

import pytest
from click.testing import CliRunner

from makelive import (
    is_live_photo_pair,
    live_id,
    make_live_photo,
    save_live_photo_pair_as_pvt,
)
from makelive.__main__ import main

TEST_IMAGE: pathlib.Path = pathlib.Path("tests/test.jpeg")
TEST_VIDEO_MP4: pathlib.Path = pathlib.Path("tests/test.mp4")
TEST_VIDEO_MOV: pathlib.Path = pathlib.Path("tests/test.mov")

TEST_IMAGE_HEIC: pathlib.Path = pathlib.Path("tests/test2.heic")
TEST_VIDEO_HEIC: pathlib.Path = pathlib.Path("tests/test2.mov")


@cache
def get_exiftool_path():
    """Return the path to exiftool"""
    return shutil.which("exiftool")


def get_metadata_with_exiftool(file_path: str) -> dict:
    process = subprocess.Popen(
        ["exiftool", "-j", "-G", file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    stdout, stderr = process.communicate()

    # ExifTool always returns a json array (even when there is just one item)
    metadata = json.loads(stdout)[0]
    return metadata


def copy_test_images(
    filepath: str | os.PathLike,
) -> tuple[str, str, str]:
    """Copy test images to a new location"""
    filepath = pathlib.Path(filepath)

    shutil.copyfile(TEST_IMAGE, filepath / TEST_IMAGE.name)
    shutil.copyfile(TEST_VIDEO_MP4, filepath / TEST_VIDEO_MP4.name)
    shutil.copyfile(TEST_VIDEO_MOV, filepath / TEST_VIDEO_MOV.name)

    return (
        str(filepath / TEST_IMAGE.name),
        str(filepath / TEST_VIDEO_MOV.name),
        str(filepath / TEST_VIDEO_MP4.name),
    )


def copy_test_images_heic(
    filepath: str | os.PathLike,
) -> tuple[str, str]:
    """Copy test images to a new location"""
    filepath = pathlib.Path(filepath)

    shutil.copyfile(TEST_IMAGE_HEIC, filepath / TEST_IMAGE_HEIC.name)
    shutil.copyfile(TEST_VIDEO_HEIC, filepath / TEST_VIDEO_HEIC.name)

    return str(filepath / TEST_IMAGE_HEIC.name), str(filepath / TEST_VIDEO_HEIC.name)


def clean_metadata_dict(metadata: dict[str, Any]) -> dict[str, Any]:
    """Clean out metadata that we don't care about because it changes between runs"""
    metadata = metadata.copy()
    for key in [
        "File:FileModifyDate",
        "File:FileAccessDate",
        "File:FileInodeChangeDate",
        "File:CurrentIPTCDigest",
    ]:
        if key in metadata:
            del metadata[key]
    for key in [
        "Photoshop:IPTCDigest",
        "XMP:XMPToolkit",
        "MakerNotes:ContentIdentifier",
    ]:
        if key in metadata:
            del metadata[key]
    return metadata


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_make_live_photo_image(tmp_path):
    """Test make_live_photo with an image"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    metadata_before = get_metadata_with_exiftool(test_image)
    asset_id = make_live_photo(test_image, test_video)
    metadata_after = get_metadata_with_exiftool(test_image)
    assert asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    for key in ["EXIF:ImageDescription", "XMP:Subject", "IPTC:Keywords"]:
        assert metadata_before.get(key, None) == metadata_after.get(key, None)


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_make_live_photo_image_heic(tmp_path):
    """Test make_live_photo with a HEIC image"""

    test_image, test_video = copy_test_images_heic(tmp_path)
    metadata_before = get_metadata_with_exiftool(test_image)
    asset_id = make_live_photo(test_image, test_video)
    metadata_after = get_metadata_with_exiftool(test_image)
    assert asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    for key in ["EXIF:ImageDescription", "XMP:Subject", "IPTC:Keywords"]:
        assert metadata_before.get(key, None) == metadata_after.get(key, None)


# @pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
# def test_make_live_photo_image_heic_no_dict(tmp_path):
#     """Test make_live_photo with a HEIC image that has no metadata dict"""
#     # the code isn't currently able to handle this case
#     test_image, test_video = copy_test_images_heic(tmp_path)
#     # wipe the metadata dict with exiftool -all= test_image
#     process = subprocess.Popen(
#         ["exiftool", "-all=", test_image],
#         stdout=subprocess.PIPE,
#         stderr=subprocess.STDOUT,
#     )
#     stdout, stderr = process.communicate()
#     asset_id = make_live_photo(test_image, test_video)
#     metadata_after = get_metadata_with_exiftool(test_image)
#     assert asset_id == metadata_after["MakerNotes:ContentIdentifier"]


@pytest.mark.parametrize("video", [TEST_VIDEO_MP4, TEST_VIDEO_MOV])
@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_make_live_photo_video(video, tmp_path):
    """Test make_live_photo with a video"""

    test_image, _, _ = copy_test_images(tmp_path)
    test_video = tmp_path / video.name
    asset_id = make_live_photo(test_image, test_video)
    metadata_after = get_metadata_with_exiftool(test_video)
    assert asset_id == metadata_after["QuickTime:ContentIdentifier"]

    # Note: do not test the other metadata because it is not currently preserved


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_make_live_photo_asset_id(tmp_path):
    """Test the make_live_photo() function with a user-provided asset ID"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    user_asset_id = str(uuid.uuid4()).upper()
    asset_id = make_live_photo(test_image, test_video, asset_id=user_asset_id)
    metadata_after = get_metadata_with_exiftool(test_image)
    assert asset_id == user_asset_id
    assert asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    metadata_after = get_metadata_with_exiftool(test_video)
    assert asset_id == metadata_after["QuickTime:ContentIdentifier"]


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_is_live_photo_pair(tmp_path):
    """Test is_live_photo_pair with an image"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    assert not is_live_photo_pair(test_image, test_video)
    asset_id = make_live_photo(test_image, test_video)
    assert is_live_photo_pair(test_image, test_video) == asset_id


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_live_id(tmp_path):
    """Test live_id with an image"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    assert not live_id(test_image)
    asset_id = make_live_photo(test_image, test_video)
    assert live_id(test_image) == asset_id


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_save_live_photo_pair_as_pvt(tmp_path):
    """Test the save_live_photo_pair_as_pvt() function"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    asset_id, pvt_file = save_live_photo_pair_as_pvt(test_image, test_video)
    metadata_after = get_metadata_with_exiftool(pvt_file / pathlib.Path(test_image).name)
    assert asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    metadata_after = get_metadata_with_exiftool(pvt_file / pathlib.Path(test_video).name)
    assert asset_id == metadata_after["QuickTime:ContentIdentifier"]

    # verify originals were not modified
    assert not is_live_photo_pair(test_image, test_video)


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_save_live_photo_pair_as_pvt_asset_id(tmp_path):
    """Test the save_live_photo_pair_as_pvt() function with user supplied asset_id"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    user_asset_id = str(uuid.uuid4()).upper()
    asset_id, pvt_file = save_live_photo_pair_as_pvt(test_image, test_video, asset_id=user_asset_id)
    metadata_after = get_metadata_with_exiftool(pvt_file / pathlib.Path(test_image).name)
    assert asset_id == user_asset_id
    assert user_asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    metadata_after = get_metadata_with_exiftool(pvt_file / pathlib.Path(test_video).name)
    assert user_asset_id == metadata_after["QuickTime:ContentIdentifier"]


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_save_live_photo_pair_as_pvt_pvt_path(tmp_path):
    """Test the save_live_photo_pair_as_pvt() function with user supplied pvt_path"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    asset_id, pvt_file = save_live_photo_pair_as_pvt(test_image, test_video, pvt_path=tmp_path)
    metadata_after = get_metadata_with_exiftool(pvt_file / pathlib.Path(test_image).name)
    assert asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    metadata_after = get_metadata_with_exiftool(pvt_file / pathlib.Path(test_video).name)
    assert asset_id == metadata_after["QuickTime:ContentIdentifier"]


def test_cli_manual(tmp_path):
    """Test the CLI with --manual"""

    test_image, test_video, _ = copy_test_images(tmp_path)

    runner = CliRunner()
    results = runner.invoke(main, ["--verbose", "--manual", test_image, test_video])
    assert results.exit_code == 0
    assert "Wrote asset ID" in results.output


def test_cli_manual_pvt(tmp_path):
    """Test the CLI with --manual --pvt"""

    test_image, test_video, _ = copy_test_images(tmp_path)

    runner = CliRunner()
    results = runner.invoke(main, ["--verbose", "--pvt", "--manual", test_image, test_video])
    assert results.exit_code == 0
    assert "Wrote asset ID" in results.output
    assert ".pvt" in results.output

    pvt_dir = tmp_path / pathlib.Path(pathlib.Path(test_image).stem + ".pvt")
    assert pvt_dir.is_dir()
    pvt_file = pvt_dir / pathlib.Path(test_image).name
    assert pvt_file.is_file()


def test_cli_files(tmp_path):
    """Test the CLI with FILES argument"""

    copy_test_images(tmp_path)

    files = [str(f) for f in tmp_path.glob("*")]
    runner = CliRunner()
    results = runner.invoke(main, ["--verbose", *files])
    assert results.exit_code == 0
    assert "Wrote asset ID" in results.output


def test_cli_files_pvt(tmp_path):
    """Test the CLI with FILES argument and --pvt"""

    copy_test_images_heic(tmp_path)

    files = [str(f) for f in tmp_path.glob("*")]
    runner = CliRunner()
    results = runner.invoke(main, ["--verbose", "--pvt", *files])
    assert results.exit_code == 0
    assert "Wrote asset ID" in results.output
    assert ".pvt" in results.output

    pvt_dir = tmp_path / pathlib.Path(pathlib.Path(files[0]).stem + ".pvt")
    assert pvt_dir.is_dir()
    pvt_file = pvt_dir / pathlib.Path(files[0]).name
    assert pvt_file.is_file()


def test_cli_bad_files(tmp_path):
    """Test the CLI with --manual and incorrect files"""

    test_image, test_video, _ = copy_test_images(tmp_path)

    runner = CliRunner()
    results = runner.invoke(main, ["--verbose", "--manual", test_video, test_image])
    assert results.exit_code != 0
    assert "is not a JPEG or HEIC" in results.output


def test_cli_no_files():
    """Test the CLI with no files"""

    runner = CliRunner()
    results = runner.invoke(main, ["--verbose"])
    assert results.exit_code != 0
    assert "No files specified" in results.output


def test_cli_check(tmp_path):
    """Test CLI with --check"""

    test_image, test_video, _ = copy_test_images(tmp_path)

    runner = CliRunner()
    results = runner.invoke(main, ["--check", str(test_image), str(test_video)])
    assert results.exit_code == 0
    assert "are not Live Photos" in results.output

    results = runner.invoke(main, [str(test_image), str(test_video)])
    assert results.exit_code == 0
    results = runner.invoke(main, ["--check", str(test_image), str(test_video)])
    assert "are Live Photos" in results.output


# ---------------------------------------------------------------------------
# Tests for the byte-perfect HEIC path (heic_metadata.py)
# ---------------------------------------------------------------------------


import hashlib
import struct


def _walk_top_level_boxes(data: bytes):
    """Yield (type, offset, total_size) for every top-level ISOBMFF box."""
    p = 0
    end = len(data)
    while p < end:
        if p + 8 > end:
            break
        size = struct.unpack(">I", data[p : p + 4])[0]
        type_ = data[p + 4 : p + 8].decode("latin1", errors="replace")
        if size == 1:
            size = struct.unpack(">Q", data[p + 8 : p + 16])[0]
        elif size == 0:
            size = end - p
        yield type_, p, size
        p += size


def _find_top_level_box(data: bytes, type_: str):
    for box_type, offset, size in _walk_top_level_boxes(data):
        if box_type == type_:
            return offset, size
    return None


def _mdat_payload(data: bytes) -> bytes:
    """Return the content of the file's `mdat` box (excluding the box header).

    Handles both 32-bit and 64-bit (extended) size-header forms; the bundled
    iPhone HEIC's mdat uses the extended form because the box is > 2 MiB.
    """
    box = _find_top_level_box(data, "mdat")
    assert box is not None, "no mdat box in file"
    offset, size = box
    declared_size = struct.unpack(">I", data[offset : offset + 4])[0]
    header_size = 16 if declared_size == 1 else 8
    return data[offset + header_size : offset + size]


def test_heic_mdat_content_is_byte_preserved(tmp_path):
    """Every byte that was in the original mdat (HEVC bitstream + item data)
    must still be present unchanged after make_live_photo.

    The new mdat may be longer than the original — the appended EXIF blob
    lives at its tail — but the original mdat region is a strict prefix of
    the new one and is byte-identical.
    """

    test_image, test_video = copy_test_images_heic(tmp_path)
    original = pathlib.Path(test_image).read_bytes()
    asset_id = make_live_photo(test_image, test_video)
    assert asset_id

    modified = pathlib.Path(test_image).read_bytes()
    orig_mdat = _mdat_payload(original)
    new_mdat = _mdat_payload(modified)

    assert len(new_mdat) >= len(orig_mdat), (
        f"new mdat ({len(new_mdat)}) is smaller than original ({len(orig_mdat)})"
    )
    assert new_mdat[: len(orig_mdat)] == orig_mdat, (
        "original mdat content (HEVC bitstream) was modified"
    )
    # Belt-and-braces hash check on the prefix.
    assert (
        hashlib.sha256(new_mdat[: len(orig_mdat)]).hexdigest()
        == hashlib.sha256(orig_mdat).hexdigest()
    )


def test_heic_all_bytes_enclosed_in_top_level_boxes(tmp_path):
    """No byte should sit outside a top-level ISOBMFF box.

    Strict ISOBMFF readers (e.g. Adobe Camera Raw) reject files with trailing
    unenclosed data; mdat must be grown to swallow any appended EXIF blob.
    """

    test_image, test_video = copy_test_images_heic(tmp_path)
    make_live_photo(test_image, test_video)
    data = pathlib.Path(test_image).read_bytes()

    last_end = 0
    for _, offset, size in _walk_top_level_boxes(data):
        assert offset == last_end, (
            f"gap between boxes (expected next box at {last_end}, found at {offset})"
        )
        last_end = offset + size
    assert last_end == len(data), (
        f"{len(data) - last_end} byte(s) past the final top-level box (orphaned data)"
    )


def test_heic_round_trip_with_live_id(tmp_path):
    """live_id() must return the asset id make_live_photo just wrote."""

    test_image, test_video = copy_test_images_heic(tmp_path)
    asset_id = make_live_photo(test_image, test_video)
    assert live_id(test_image) == asset_id
    assert live_id(test_video) == asset_id
    assert is_live_photo_pair(test_image, test_video) == asset_id


def test_heic_replace_existing_content_identifier(tmp_path):
    """Running twice with different ids replaces the value, doesn't accumulate."""

    test_image, test_video = copy_test_images_heic(tmp_path)
    first = make_live_photo(test_image, test_video)
    second = make_live_photo(test_image, test_video, asset_id=str(uuid.uuid4()).upper())
    assert first != second
    assert live_id(test_image) == second
    assert is_live_photo_pair(test_image, test_video) == second


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_heic_preserves_existing_exif(tmp_path):
    """Make/Model/timestamp/etc. set by the camera survive our edit."""

    test_image, test_video = copy_test_images_heic(tmp_path)
    before = get_metadata_with_exiftool(test_image)
    make_live_photo(test_image, test_video)
    after = get_metadata_with_exiftool(test_image)

    # Anything camera-set should round-trip unchanged. Only fail when a field
    # exists before and changes after; silently-missing fields are fine.
    for key in [
        "EXIF:Make",
        "EXIF:Model",
        "EXIF:DateTimeOriginal",
        "EXIF:CreateDate",
        "EXIF:ImageDescription",
    ]:
        if key in before:
            assert before[key] == after.get(key), f"{key} changed after edit"


def test_heic_metadata_module_raises_on_invalid_file(tmp_path):
    """The byte-perfect path should error clearly on files it can't handle."""

    from makelive.heic_metadata import set_heic_content_identifier

    bogus = tmp_path / "bogus.heic"
    bogus.write_bytes(b"not a valid heic file" + b"\x00" * 100)
    with pytest.raises(Exception):
        set_heic_content_identifier(bogus, "ABCDEF01-2345-6789-ABCD-EF0123456789")


def test_heic_size_growth_is_bounded(tmp_path):
    """File should grow by ~the size of the new EXIF blob — bounded to a few KiB."""

    test_image, test_video = copy_test_images_heic(tmp_path)
    orig_size = pathlib.Path(test_image).stat().st_size
    make_live_photo(test_image, test_video)
    new_size = pathlib.Path(test_image).stat().st_size

    # A single MakerNote entry plus a serialised EXIF IFD is well under 4 KiB
    # for any reasonable source. Catches a regression where the rewrite would
    # append a large amount of data.
    growth = new_size - orig_size
    assert -4096 < growth < 4096, f"unexpected file size delta: {growth} bytes"


# ---------------------------------------------------------------------------
# Tests for the byte-perfect MP4 / QuickTime path (mp4_metadata.py)
#
# The video path's correctness properties mirror the HEIC ones:
#   * the `mdat` box is untouched (HEVC / H.264 bitstream is byte-identical)
#   * no bytes sit outside a top-level box
#   * the ContentIdentifier round-trips correctly via live_id() and is_live_photo_pair()
#   * running twice replaces the value rather than accumulating duplicate keys
# ---------------------------------------------------------------------------


def _mp4_mdat_payload(data: bytes) -> bytes:
    """Like _mdat_payload but doesn't assume the bundled HEIC's 64-bit form;
    works on every form (compact 32-bit / 64-bit extended / size-to-end-of-file)."""
    return _mdat_payload(data)


@pytest.mark.parametrize("video", [TEST_VIDEO_MP4, TEST_VIDEO_MOV])
def test_mp4_mdat_is_byte_identical(video, tmp_path):
    """The encoded video samples inside mdat must be untouched.

    For QuickTime / MP4 the rewrite never moves or extends mdat: the original
    moov region becomes a `free` box of identical size, and the new moov is
    appended after mdat. So mdat is byte-identical, not just a prefix-match.
    """

    test_image, _, _ = copy_test_images(tmp_path)
    test_video = tmp_path / video.name
    original = pathlib.Path(test_video).read_bytes()
    asset_id = make_live_photo(test_image, str(test_video))
    assert asset_id

    modified = pathlib.Path(test_video).read_bytes()
    orig_mdat = _mp4_mdat_payload(original)
    new_mdat = _mp4_mdat_payload(modified)

    assert orig_mdat == new_mdat, "mdat content (compressed video samples) was modified"
    assert hashlib.sha256(orig_mdat).hexdigest() == hashlib.sha256(new_mdat).hexdigest()


@pytest.mark.parametrize("video", [TEST_VIDEO_MP4, TEST_VIDEO_MOV])
def test_mp4_all_bytes_enclosed_in_top_level_boxes(video, tmp_path):
    """Every byte of the rewritten file must sit inside a top-level box."""

    test_image, _, _ = copy_test_images(tmp_path)
    test_video = tmp_path / video.name
    make_live_photo(test_image, str(test_video))
    data = pathlib.Path(test_video).read_bytes()

    last_end = 0
    for _, offset, size in _walk_top_level_boxes(data):
        assert offset == last_end, (
            f"gap between boxes (expected next box at {last_end}, found at {offset})"
        )
        last_end = offset + size
    assert last_end == len(data), (
        f"{len(data) - last_end} byte(s) past the final top-level box (orphaned data)"
    )


@pytest.mark.parametrize("video", [TEST_VIDEO_MP4, TEST_VIDEO_MOV])
def test_mp4_round_trip_with_live_id(video, tmp_path):
    """live_id() returns the asset id make_live_photo just wrote."""

    test_image, _, _ = copy_test_images(tmp_path)
    test_video = tmp_path / video.name
    asset_id = make_live_photo(test_image, str(test_video))
    assert live_id(test_image) == asset_id
    assert live_id(str(test_video)) == asset_id
    assert is_live_photo_pair(test_image, str(test_video)) == asset_id


@pytest.mark.parametrize("video", [TEST_VIDEO_MP4, TEST_VIDEO_MOV])
def test_mp4_replace_existing_content_identifier(video, tmp_path):
    """Running twice with different ids replaces the value, doesn't accumulate
    duplicate `keys` / `ilst` entries inside moov/meta."""

    test_image, _, _ = copy_test_images(tmp_path)
    test_video = tmp_path / video.name
    first = make_live_photo(test_image, str(test_video))
    second = make_live_photo(
        test_image, str(test_video), asset_id=str(uuid.uuid4()).upper()
    )
    assert first != second
    assert live_id(str(test_video)) == second
    assert is_live_photo_pair(test_image, str(test_video)) == second


def test_mp4_metadata_module_raises_on_missing_moov(tmp_path):
    """The byte-perfect path should error clearly on files it can't handle."""

    from makelive.mp4_metadata import set_mp4_content_identifier

    bogus = tmp_path / "bogus.mov"
    # Looks like an ISOBMFF file (ftyp present) but no moov.
    bogus.write_bytes(
        b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  \x00\x00\x00\x00"
        + b"\x00\x00\x00\x10mdat" + b"\x00" * 8
    )
    with pytest.raises(ValueError):
        set_mp4_content_identifier(bogus, "ABCDEF01-2345-6789-ABCD-EF0123456789")


@pytest.mark.parametrize("video", [TEST_VIDEO_MP4, TEST_VIDEO_MOV])
def test_mp4_size_growth_is_bounded(video, tmp_path):
    """File grows by at most a couple of KiB (the new moov, if larger than
    the old one, is appended; otherwise the rewrite is in-place)."""

    test_image, _, _ = copy_test_images(tmp_path)
    test_video = tmp_path / video.name
    orig_size = pathlib.Path(test_video).stat().st_size
    make_live_photo(test_image, str(test_video))
    new_size = pathlib.Path(test_video).stat().st_size
    growth = new_size - orig_size
    # An entirely-fresh meta box with one mdta key/value entry is well under 8 KiB.
    assert -8192 < growth < 8192, f"unexpected file size delta: {growth} bytes"
