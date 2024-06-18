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

from makelive import is_live_photo_pair, live_id, make_live_photo, make_pvt
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
def test_make_pvt(tmp_path):
    """Test the make_pvt() function"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    pvt_file = make_pvt(test_image, test_video)
    metadata_after = get_metadata_with_exiftool(
        pvt_file / pathlib.Path(test_image).name
    )
    assert metadata_after["MakerNotes:ContentIdentifier"]
    metadata_after = get_metadata_with_exiftool(
        pvt_file / pathlib.Path(test_video).name
    )
    assert metadata_after["QuickTime:ContentIdentifier"]


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_make_pvt_asset_id(tmp_path):
    """Test the make_pvt() function with user supplied asset_id"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    user_asset_id = str(uuid.uuid4()).upper()
    pvt_file = make_pvt(test_image, test_video, asset_id=user_asset_id)
    metadata_after = get_metadata_with_exiftool(
        pvt_file / pathlib.Path(test_image).name
    )
    assert user_asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    metadata_after = get_metadata_with_exiftool(
        pvt_file / pathlib.Path(test_video).name
    )
    assert user_asset_id == metadata_after["QuickTime:ContentIdentifier"]


@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_make_pvt_pvt_path(tmp_path):
    """Test the make_pvt() function with user supplied pvt_path"""

    test_image, test_video, _ = copy_test_images(tmp_path)
    pvt_file = make_pvt(test_image, test_video, pvt_path=tmp_path)
    metadata_after = get_metadata_with_exiftool(
        pvt_file / pathlib.Path(test_image).name
    )
    assert metadata_after["MakerNotes:ContentIdentifier"]
    metadata_after = get_metadata_with_exiftool(
        pvt_file / pathlib.Path(test_video).name
    )
    assert metadata_after["QuickTime:ContentIdentifier"]


def test_cli_manual(tmp_path):
    """Test the CLI with --manual"""

    test_image, test_video, _ = copy_test_images(tmp_path)

    runner = CliRunner()
    results = runner.invoke(main, ["--verbose", "--manual", test_image, test_video])
    assert results.exit_code == 0
    assert "Wrote asset ID" in results.output


def test_cli_files(tmp_path):
    """Test the CLI with FILES argument"""

    copy_test_images(tmp_path)

    files = [str(f) for f in tmp_path.glob("*")]
    runner = CliRunner()
    results = runner.invoke(main, ["--verbose", *files])
    assert results.exit_code == 0
    assert "Wrote asset ID" in results.output


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
