"""Add the necessary metadata to photo + video pair so Photos recognizes them as Live Photos when imported"""

from __future__ import annotations

import os
import pathlib
import shutil
import uuid

import AVFoundation
import cgmetadata
import objc
import Quartz
from Foundation import (
    NSURL,
    NSData,
    NSMutableData,
    NSMutableDictionary,
)
from wurlitzer import pipes

from .heic_metadata import set_heic_content_identifier
from .mp4_metadata import set_mp4_content_identifier

# Constants
# key for the MakerApple dictionary in the image metadata to store the asset ID
# exiftool reports this as MakerNote:ContentIdentifier
kFigAppleMakerNote_AssetIdentifier = "17"

# key and key space for the asset ID in the QuickTime movie metadata
kKeyContentIdentifier = "com.apple.quicktime.content.identifier"
kKeySpaceQuickTimeMetadata = "mdta"

# Auxiliary data attached to the main image that should be preserved when
# rewriting the file (CGImageDestinationAddImageFromSource drops these by
# default). HDR gain maps and depth maps are the most user-visible.
_AUXILIARY_DATA_TYPES = (
    Quartz.kCGImageAuxiliaryDataTypeHDRGainMap,
    Quartz.kCGImageAuxiliaryDataTypeISOGainMap,
    Quartz.kCGImageAuxiliaryDataTypeDepth,
    Quartz.kCGImageAuxiliaryDataTypeDisparity,
    Quartz.kCGImageAuxiliaryDataTypePortraitEffectsMatte,
    Quartz.kCGImageAuxiliaryDataTypeSemanticSegmentationSkinMatte,
    Quartz.kCGImageAuxiliaryDataTypeSemanticSegmentationHairMatte,
    Quartz.kCGImageAuxiliaryDataTypeSemanticSegmentationTeethMatte,
    Quartz.kCGImageAuxiliaryDataTypeSemanticSegmentationGlassesMatte,
    Quartz.kCGImageAuxiliaryDataTypeSemanticSegmentationSkyMatte,
)

### Functions for adding asset id to image file ###


def add_asset_id_to_image_file(
    image_path: str | os.PathLike,
    asset_id: str,
) -> None:
    """Write the MakerApple ContentIdentifier to the image file in place.

    Per-format pipeline:
      • HEIC / HEIF: byte-level ISOBMFF surgery (see `heic_metadata`). The
        HEVC bitstream is byte-preserved; only the EXIF item is rewritten
        and the `iloc` updated. Gain map / depth / all auxiliary items
        survive trivially because they are never touched.
      • JPEG / other: CGImageDestinationAddImageFromSource — the only CG
        path that can write MakerNote. For JPEG→JPEG this is a passthrough
        copy at default settings; `PreserveGainMap` + an explicit
        auxiliary-data copy loop keep HDR / depth around.
    """
    image_path = pathlib.Path(image_path)
    if image_path.suffix.lower() in (".heic", ".heif"):
        set_heic_content_identifier(image_path, asset_id)
        return

    image_path = str(image_path)
    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(image_path)
        source = Quartz.CGImageSourceCreateWithURL(url, None)
        if not source:
            raise ValueError(f"Could not create image source for {image_path}")

        properties = Quartz.CGImageSourceCopyPropertiesAtIndex(source, 0, None)
        mutable_props = (
            properties.mutableCopy()
            if properties is not None
            else NSMutableDictionary.alloc().init()
        )

        maker_apple = mutable_props.objectForKey_(Quartz.kCGImagePropertyMakerAppleDictionary)
        maker_apple = maker_apple.mutableCopy() if maker_apple else NSMutableDictionary.alloc().init()
        maker_apple.setObject_forKey_(asset_id, kFigAppleMakerNote_AssetIdentifier)
        mutable_props.setObject_forKey_(
            maker_apple, Quartz.kCGImagePropertyMakerAppleDictionary
        )

        # HEIC/HEIF go through the byte-perfect path above; this branch is
        # JPEG (and any other CG-supported format), where the default-quality
        # AddImageFromSource produces a passthrough copy.
        mutable_props.setObject_forKey_(False, Quartz.kCGImageDestinationOptimizeColorForSharing)
        mutable_props.setObject_forKey_(True, Quartz.kCGImageDestinationPreserveGainMap)

        image_type = Quartz.CGImageSourceGetType(source)
        dest_data = NSMutableData.data()
        destination = Quartz.CGImageDestinationCreateWithData(dest_data, image_type, 1, None)
        if not destination:
            raise ValueError(f"Could not create image destination for {image_path}")

        with pipes() as (_out, _err):
            # CG may emit harmless AVEBridge messages to stderr for some HEIC files.
            # https://forums.developer.apple.com/forums/thread/722204
            Quartz.CGImageDestinationAddImageFromSource(
                destination, source, 0, mutable_props
            )
            # PreserveGainMap covers the HDR gain map; depth/portrait/segmentation
            # mattes still need to be re-attached explicitly or they get dropped.
            for aux_type in _AUXILIARY_DATA_TYPES:
                if aux_type is None:
                    continue
                info = Quartz.CGImageSourceCopyAuxiliaryDataInfoAtIndex(source, 0, aux_type)
                if info is not None:
                    Quartz.CGImageDestinationAddAuxiliaryDataInfo(destination, aux_type, info)
            if not Quartz.CGImageDestinationFinalize(destination):
                raise ValueError(f"Could not finalize image destination for {image_path}")

        NSData.dataWithData_(dest_data).writeToFile_atomically_(image_path, True)


### Functions for adding asset id to QuickTime video file ###


def avmetadata_for_asset_id(asset_id: str) -> AVFoundation.AVMetadataItem:
    """Create an AVMetadataItem for the given asset id

    Args:
        asset_id: The asset id to write to the file.

    Returns: AVMetadataItem with the asset id.
    """
    item = AVFoundation.AVMutableMetadataItem.metadataItem()
    item.setKey_(kKeyContentIdentifier)
    item.setKeySpace_(kKeySpaceQuickTimeMetadata)
    item.setValue_(asset_id)
    item.setDataType_("com.apple.metadata.datatype.UTF-8")
    return item


def add_asset_id_to_quicktime_file(filepath: str | os.PathLike, asset_id: str) -> str | None:
    """Write the asset id to a QuickTime movie file at filepath and save to destination path.

    Args:
        filepath: Path to the QuickTime movie file.
        asset_id: The asset id to write to the file.

    Returns: Error message if there was an error, otherwise None.

    For `.mov` files, uses AVMutableMovie.writeMovieHeaderToURL to rewrite only
    the movie header in place. All track data, track reference atoms (tref),
    and mebx metadata tracks are preserved exactly as they were in the
    original file. iOS requires the cdsc/cdep tref associations between mebx
    timed-metadata tracks and the video track to enable lock screen Live
    Wallpaper animation (the "animate" button).

    For `.mp4` files, uses byte-level ISOBMFF surgery in `mp4_metadata`. The
    HEVC bitstream inside `mdat` is byte-identical to the source; only `moov`
    is rewritten (in place when possible, or as a free-box + appended new
    moov otherwise). No re-encoding, no track-data remux.
    """
    filepath = pathlib.Path(filepath)
    if filepath.suffix.lower() != ".mov":
        try:
            set_mp4_content_identifier(filepath, asset_id)
            return None
        except Exception as e:
            return str(e)

    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(str(filepath))
        movie, error = AVFoundation.AVMutableMovie.movieWithURL_options_error_(url, None, None)
        if movie is None:
            return f"Could not open {filepath} as AVMutableMovie: {error.description() if error else 'unknown error'}"

        # Replace any existing content identifier, preserving all other movie-level metadata
        existing = [
            item
            for item in (movie.metadata() or [])
            if not (str(item.key()) == kKeyContentIdentifier and str(item.keySpace()) == kKeySpaceQuickTimeMetadata)
        ]
        movie.setMetadata_(existing + [avmetadata_for_asset_id(asset_id)])

        # Write only the movie header back to the same URL.
        # This updates the atom structure in place without re-encoding or re-interleaving
        # any track data, so all tref associations between mebx and video tracks are preserved.
        success, error = movie.writeMovieHeaderToURL_fileType_options_error_(url, AVFoundation.AVFileTypeQuickTimeMovie, 0, None)
        if not success:
            return f"writeMovieHeaderToURL failed for {filepath}: {error.description() if error else 'unknown error'}"
        return None


def is_image_file(filepath: str | os.PathLike):
    """Return True if the file is a JPEG or HEIC image file"""
    filepath = pathlib.Path(filepath)
    return filepath.suffix.lower() in [".jpg", ".jpeg", ".heic", ".heif"]


def is_video_file(filepath: str | os.PathLike):
    """Return True if the file is a MOV or MP4 video file"""
    filepath = pathlib.Path(filepath)
    return filepath.suffix.lower() in [".mov", ".mp4"]


### Public API ###


def make_live_photo(
    image_path: str | os.PathLike,
    video_path: str | os.PathLike,
    asset_id: str | None = None,
) -> str:
    """Given a JPEG/HEIC image and a QuickTime video, add the necessary metadata to make it a Live Photo

    Args:
        image_path: Path to the image file.
        video_path: Path to the QuickTime movie file.
        asset_id: The asset id to write to the file; if not provided a unique asset will be created.

    Returns: The asset id (content identifier) written to the photo + video pair.

    Raises:
        FileNotFoundError: If image_path or video_path do not exist.
        ValueError: If image_path is not a JPEG or HEIC image or video_path is not a QuickTime movie file.

    Note:
        If asset_id is not provided, a unique asset id will be generated and used.
        The asset_id is written to the ContentIdentifier metadata in the image and video files.
        If the image or video already have a ContentIdentifier, it will be overwritten.
        The image and video files will be modified in place.

        Note: XMP metadata in the QuickTime movie file is not preserved by this function which
        may result in metadata loss.

        Metadata including EXIF, IPTC, and XMP are preserved in the image file but will be rewritten
        and the Core Graphics API may change the order of the metadata and normalize the values.
        For example, the tag XMP:TagsList will be rewritten as XMP:Subject and the value will be
        normalized to a list of title case strings.

        If you must preserve the original metadata completely, it is recommended to make a copy of the
        metadata using a tool like exiftool before calling this function and then restore the metadata
        after calling this function. (But take care not to delete the ContentIdentifier metadata.)
    """
    image_path = pathlib.Path(image_path)
    video_path = pathlib.Path(video_path)
    if not image_path.exists():
        raise FileNotFoundError(f"{image_path} does not exist")
    if not video_path.exists():
        raise FileNotFoundError(f"{video_path} does not exist")
    if not is_image_file(image_path):
        raise ValueError(f"{image_path} is not a JPEG or HEIC image")
    if not is_video_file(video_path):
        raise ValueError(f"{video_path} is not a QuickTime movie file")
    asset_id = asset_id or str(uuid.uuid4()).upper()
    add_asset_id_to_image_file(image_path, asset_id)
    add_asset_id_to_quicktime_file(video_path, asset_id)
    return asset_id


def save_live_photo_pair_as_pvt(
    image_path: str | os.PathLike,
    video_path: str | os.PathLike,
    pvt_path: str | os.PathLike | None = None,
    asset_id: str | None = None,
) -> tuple[str, pathlib.Path]:
    """Given a JPEG/HEIC image and a QuickTime video, add the necessary metadata to make it a Live Photo
    and package as a .pvt package which can be double-clicked to import into Photos as a Live Photo.

    Args:
        image_path: Path to the image file.
        video_path: Path to the QuickTime movie file.
        pvt_path: Path to directory in which to write the .pvt package file; if None, writes the .pvt file in the parent of the image_path.
        asset_id: The asset id to write to the file; if not provided a unique asset will be created.

    Returns: Tuple of Asset ID, Path to the .pvt package file.

    Raises:
        FileNotFoundError: If image_path or video_path do not exist.
        ValueError: If image_path is not a JPEG or HEIC image or video_path is not a QuickTime movie file.

    Note:
        The .pvt package will have the same stem as the image file with a .pvt extension.
        If asset_id is not provided, a unique asset id will be generated and used.
        The asset_id is written to the ContentIdentifier metadata in the image and video files.
        If the image or video already have a ContentIdentifier, it will be overwritten.
        The image and video files will be modified in place.

        Note: XMP metadata in the QuickTime movie file is not preserved by this function which
        may result in metadata loss.

        Metadata including EXIF, IPTC, and XMP are preserved in the image file but will be rewritten
        and the Core Graphics API may change the order of the metadata and normalize the values.
        For example, the tag XMP:TagsList will be rewritten as XMP:Subject and the value will be
        normalized to a list of title case strings.

        If you must preserve the original metadata completely, it is recommended to make a copy of the
        metadata using a tool like exiftool before calling this function and then restore the metadata
        after calling this function. (But take care not to delete the ContentIdentifier metadata.)
    """
    image_path = pathlib.Path(image_path)
    video_path = pathlib.Path(video_path)
    pvt_path = pathlib.Path(pvt_path) if pvt_path else image_path.parent
    pvt_package = pvt_path / f"{image_path.stem}.pvt"
    return _make_pvt_package(image_path, video_path, pvt_package, asset_id)


def _make_pvt_package(
    image_path: pathlib.Path,
    video_path: pathlib.Path,
    pvt_path: pathlib.Path,
    asset_id: str | None = None,
) -> tuple[str, pathlib.Path]:
    """Create a .pvt Live Photo package from an image and video file."""
    pvt_path.mkdir(exist_ok=True)
    shutil.copy(image_path, pvt_path)
    shutil.copy(video_path, pvt_path)
    image_path = pvt_path / image_path.name
    video_path = pvt_path / video_path.name

    # if not already a Live Pair or asset_id is not None, make it a Live Pair with the asset_id if provided
    if not is_live_photo_pair(image_path, video_path) or asset_id is not None:
        asset_id = make_live_photo(image_path, video_path, asset_id)
    else:
        asset_id = live_id(image_path)

    # create the metadata.plist file
    xml_metadata = """
        <?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
            <dict>
                <key>PFVideoComplementMetadataVersionKey</key>
                <string>1</string>
            </dict>
        </plist>
        """

    with open(pvt_path / "metadata.plist", "w") as metadata_file:
        metadata_file.write(xml_metadata)

    return asset_id, pvt_path


def live_id(filepath: str | os.PathLike) -> str | None:
    """Returns the Live Photo Content Identifier for the file or None

    Args:
        filepath: Path to the image or video file.

    Returns: The content identifier for the Live Photo or None if not found.

    Note: The content identifier (stored in Maker Notes with key "17" for images and
        in QuickTime metadata for videos) is used by Photos to link the image and video files
        together as a Live Photo.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not a JPEG/HEIC image or MOV/MP4 video file.
    """
    filepath = pathlib.Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"{filepath} does not exist")

    if is_image_file(filepath):
        md = cgmetadata.ImageMetadata(filepath)
        try:
            return md.asdict()["MakerApple"]["17"]
        except KeyError:
            return None
    elif is_video_file(filepath):
        with objc.autorelease_pool():
            url = NSURL.fileURLWithPath_(str(filepath))
            asset = AVFoundation.AVAsset.assetWithURL_(url)
            for item in asset.metadata():
                if item.key() == kKeyContentIdentifier and item.keySpace() == kKeySpaceQuickTimeMetadata:
                    return str(item.value())
        return None
    else:
        raise ValueError(f"{filepath} is not a JPEG/HEIC image or MOV/MP4 video file")


def is_live_photo_pair(image_path: str | os.PathLike, video_path: str | os.PathLike) -> str | bool:
    """Check if the image and video pair are a Live Photo

    Args:
        image_path: Path to the image file.
        video_path: Path to the QuickTime movie file.

    Returns: Asset ID if the file pair is a Live Photo (truthy value), False otherwise.

    Raises:
        FileNotFoundError: If image_path or video_path does not exist.
        ValueError: If image_path is not a JPEG or HEIC image or video_path is not a QuickTime movie file.
    """
    image_path = pathlib.Path(image_path)
    video_path = pathlib.Path(video_path)
    if not image_path.exists():
        raise FileNotFoundError(f"{image_path} does not exist")
    if not video_path.exists():
        raise FileNotFoundError(f"{video_path} does not exist")

    if not is_image_file(image_path):
        raise ValueError("Image file is not a JPEG or HEIC image")
    if not is_video_file(video_path):
        raise ValueError("Video file is not a QuickTime movie file")

    if image_id := live_id(image_path):
        if video_id := live_id(video_path):
            return image_id if image_id == video_id else False
    return False
