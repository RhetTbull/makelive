"""Test makelive.py"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

from makelive import make_live_photo

TEST_IMAGE: pathlib.Path = pathlib.Path("tests/test.jpeg")
TEST_VIDEO_MP4: pathlib.Path = pathlib.Path("tests/test.mp4")
TEST_VIDEO_MOV: pathlib.Path = pathlib.Path("tests/test.mov")


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

def copy_test_images(filepath: str | os.PathLike):
    """Copy test images to a new location"""
    filepath = pathlib.Path(filepath)

    shutil.copyfile(TEST_IMAGE, filepath / TEST_IMAGE.name)
    shutil.copyfile(TEST_VIDEO_MP4, filepath / TEST_VIDEO_MP4.name)
    shutil.copyfile(TEST_VIDEO_MOV, filepath / TEST_VIDEO_MOV.name)

# @pytest.mark.parametrize("video", [TEST_VIDEO_MP4, TEST_VIDEO_MOV])

def clean_metadata_dict(metadata: dict[str, Any]) -> dict[str, Any]:
    """Clean out metadata that we don't care about because it changes between runs"""
    metadata = metadata.copy()
    for key in ["File:FileModifyDate", "File:FileAccessDate", "File:FileInodeChangeDate", "File:CurrentIPTCDigest"]:
        if key in metadata:
            del metadata[key]
    for key in ["Photoshop:IPTCDigest","XMP:XMPToolkit", "MakerNotes:ContentIdentifier" ]:
        if key in metadata:
            del metadata[key]
    return metadata

@pytest.mark.skipif(get_exiftool_path() is None, reason="exiftool not found")
def test_make_live_photo_image(tmp_path):
    """Test make_live_photo with an image"""

    copy_test_images(tmp_path)
    test_image = tmp_path / TEST_IMAGE.name
    test_video = tmp_path / TEST_VIDEO_MOV.name
    metadata_before = get_metadata_with_exiftool(test_image)
    asset_id = make_live_photo(test_image, test_video)
    metadata_after = get_metadata_with_exiftool(test_image)
    assert asset_id == metadata_after["MakerNotes:ContentIdentifier"]
    for key in ["EXIF:ImageDescription", "XMP:Subject", "IPTC:Keywords"]:
        assert metadata_before.get(key, None) == metadata_after.get(key, None)
