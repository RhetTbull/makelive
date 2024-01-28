"""Add the necessary metadata to photo + video pair so Photos recognizes them as Live Photos when imported"""

from __future__ import annotations

import os
import pathlib
import threading
import uuid

import AVFoundation
import objc
import Quartz
from Foundation import (
    NSURL,
    CFDictionaryRef,
    NSData,
    NSMutableData,
    NSMutableDictionary,
)
from wurlitzer import pipes

# Constants
# key for the MakerApple dictionary in the image metadata to store the asset ID
# exiftool reports this as MakerNote:ContentIdentifier
kFigAppleMakerNote_AssetIdentifier = "17"

# key and key space for the asset ID in the QuickTime movie metadata
kKeyContentIdentifier = "com.apple.quicktime.content.identifier"
kKeySpaceQuickTimeMetadata = "mdta"

### Functions for adding asset id to image file ###


def image_source_from_path(
    image_path: str | os.PathLike,
) -> Quartz.CGImageSourceRef:
    """Create a CGImageSourceRef from an image file.

    Args:
        image_path: Path to the image file.

    Returns: CGImageSourceRef with the image data.

    Raises:
        ValueError: If the image source could not be created.
    """
    with objc.autorelease_pool():
        image_url = NSURL.fileURLWithPath_(str(image_path))
        image_source = Quartz.CGImageSourceCreateWithURL(image_url, None)
        if not image_source:
            raise ValueError(f"Could not create image source for {image_path}")
        return image_source


def write_image_with_metadata(
    image_data: Quartz.CGImageSourceRef,
    metadata: CFDictionaryRef,
    destination_path: str | os.PathLike,
) -> None:
    """Write image with metadata to destination path

    Args:
        image_data: CGImageSourceRef with the image data.
        metadata: CFDictionaryRef with the metadata to write.
        destination_path: Path to write the image to.

    Note:
        If destination_path already exists, it will be overwritten.

    Raises:
        ValueError: If the image destination could not be created.
    """
    destination_path = str(destination_path)
    with objc.autorelease_pool():
        image_type = Quartz.CGImageSourceGetType(image_data)
        dest_data = NSMutableData.data()
        destination = Quartz.CGImageDestinationCreateWithData(dest_data, image_type, 1, None)
        if not destination:
            raise ValueError(f"Could not create image destination for {destination_path}")
        with pipes() as (_out, _err):
            # use pipes to catch error messages from CGImageDestinationAddImageFromSource
            # there's a bug in Core Graphics that causes an error similar to
            # AVEBridge Info: AVEEncoder_CreateInstance: Received CreateInstance (from VT)
            # ... AVEBridge Error: AVEEncoder_CreateInstance: returning err = -12908
            # to output to stderr/console but the image is still written correctly
            # reference: https://github.com/biodranik/HEIF/issues/5 and
            # https://forums.developer.apple.com/forums/thread/722204
            Quartz.CGImageDestinationAddImageFromSource(destination, image_data, 0, metadata)
            Quartz.CGImageDestinationFinalize(destination)
            new_image_data = NSData.dataWithData_(dest_data)
            new_image_data.writeToFile_atomically_(destination_path, True)


def metadata_dict_for_asset_id(image_data: Quartz.CGImageSourceRef, asset_id: str) -> CFDictionaryRef:
    """Create a CFDictionaryRef with the asset id in the MakerApple dictionary and merge with existing metadata

    Args:
        image_data: CGImageSourceRef with the image data.
        asset_id: The asset id to write to the file.

    Returns: CFDictionaryRef with the new metadata dictionary.
    """
    with objc.autorelease_pool():
        metadata = Quartz.CGImageSourceCopyPropertiesAtIndex(image_data, 0, None)
        metadata_as_mutable = metadata.mutableCopy()
        maker_apple = metadata_as_mutable.objectForKey_(Quartz.kCGImagePropertyMakerAppleDictionary)
        if not maker_apple:
            maker_apple = NSMutableDictionary.alloc().init()
        maker_apple.setObject_forKey_(asset_id, kFigAppleMakerNote_AssetIdentifier)
        metadata_as_mutable.setObject_forKey_(maker_apple, Quartz.kCGImagePropertyMakerAppleDictionary)
        return metadata_as_mutable


def add_asset_id_to_image_file(
    image_path: str | os.PathLike,
    asset_id: str,
) -> None:
    """Write the asset id to file at image_path and save to destination path

    Args:
        image_path: Path to the image file.
        asset_id: The asset id to write to the file.
    """
    image_path = str(image_path)
    with objc.autorelease_pool():
        image_data = image_source_from_path(image_path)
        metadata = metadata_dict_for_asset_id(image_data, asset_id)
        write_image_with_metadata(image_data, metadata, image_path)


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
    """Write the asset id to a QuickTime movie file at filepath and save to destination path

    Args:
        filepath: Path to the QuickTime movie file.
        asset_id: The asset id to write to the file.

    Returns: Error message if there was an error, otherwise None.

    Note: XMP metadata in the QuickTime movie file is not preserved by this function which
    may result in metadata loss.
    """
    filepath = pathlib.Path(filepath)
    with objc.autorelease_pool():
        # rename file so export can write to original path
        temp_filepath = filepath.parent / f".{asset_id}_{filepath.name}"
        os.rename(filepath, temp_filepath)
        input_url = NSURL.fileURLWithPath_(str(temp_filepath))
        output_url = NSURL.fileURLWithPath_(str(filepath))
        asset = AVFoundation.AVAsset.assetWithURL_(input_url)
        metadata_item = avmetadata_for_asset_id(asset_id)
        export_session = AVFoundation.AVAssetExportSession.alloc().initWithAsset_presetName_(
            asset, AVFoundation.AVAssetExportPresetPassthrough
        )

        export_session.setOutputFileType_(AVFoundation.AVFileTypeQuickTimeMovie)
        export_session.setOutputURL_(output_url)
        export_session.setMetadata_([metadata_item])

        # exportAsynchronouslyWithCompletionHandler_ is an asynchronous method that return immediately
        # To wait for the export to complete, use a threading.Event to block until the completion handler is called.
        event = threading.Event()
        error = None

        def _completion_handler():
            nonlocal error
            if error_val := export_session.error():
                error = error_val.description()
            event.set()

        export_session.exportAsynchronouslyWithCompletionHandler_(_completion_handler)
        event.wait()

        if error:
            try:
                # filepath might not exist if export failed
                os.unlink(filepath)
            except FileNotFoundError:
                pass
            os.rename(temp_filepath, filepath)
        else:
            os.unlink(temp_filepath)

        return error or None


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
    if image_path.suffix.lower() not in [".jpg", ".jpeg", ".heic", ".heif"]:
        raise ValueError(f"{image_path} is not a JPEG or HEIC image")
    if video_path.suffix.lower() not in [".mov", ".mp4"]:
        raise ValueError(f"{video_path} is not a QuickTime movie file")
    asset_id = asset_id or str(uuid.uuid4()).upper()
    add_asset_id_to_image_file(image_path, asset_id)
    add_asset_id_to_quicktime_file(video_path, asset_id)
    return asset_id
