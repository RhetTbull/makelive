"""Add the necessary metadata to photo + video pair so Photos recognizes them as Live Photos when imported"""

from __future__ import annotations

import os
import pathlib
import threading
import uuid
import AVFoundation
import click
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

__version__ = "0.1.0"

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
        destination = Quartz.CGImageDestinationCreateWithData(
            dest_data, image_type, 1, None
        )
        if not destination:
            raise ValueError(
                f"Could not create image destination for {destination_path}"
            )
        with pipes() as (_out, _err):
            # use pipes to catch error messages from CGImageDestinationAddImageFromSource
            # there's a bug in Core Graphics that causes an error similar to
            # AVEBridge Info: AVEEncoder_CreateInstance: Received CreateInstance (from VT)
            # ... AVEBridge Error: AVEEncoder_CreateInstance: returning err = -12908
            # to output to stderr/console but the image is still written correctly
            # reference: https://github.com/biodranik/HEIF/issues/5 and
            # https://forums.developer.apple.com/forums/thread/722204
            Quartz.CGImageDestinationAddImageFromSource(
                destination, image_data, 0, metadata
            )
            Quartz.CGImageDestinationFinalize(destination)
            new_image_data = NSData.dataWithData_(dest_data)
            new_image_data.writeToFile_atomically_(destination_path, True)


def metadata_dict_for_asset_id(
    image_data: Quartz.CGImageSourceRef, asset_id: str
) -> CFDictionaryRef:
    """Create a CFDictionaryRef with the asset id in the MakerApple dictionary and merge with existing metadata

    Args:
        image_data: CGImageSourceRef with the image data.
        asset_id: The asset id to write to the file.

    Returns: CFDictionaryRef with the new metadata dictionary.
    """
    with objc.autorelease_pool():
        metadata = Quartz.CGImageSourceCopyPropertiesAtIndex(image_data, 0, None)
        metadata_as_mutable = metadata.mutableCopy()
        maker_apple = metadata_as_mutable.objectForKey_(
            Quartz.kCGImagePropertyMakerAppleDictionary
        )
        if not maker_apple:
            maker_apple = NSMutableDictionary.alloc().init()
        maker_apple.setObject_forKey_(asset_id, kFigAppleMakerNote_AssetIdentifier)
        metadata_as_mutable.setObject_forKey_(
            maker_apple, Quartz.kCGImagePropertyMakerAppleDictionary
        )
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


def add_asset_id_to_quicktime_file(
    filepath: str | os.PathLike, asset_id: str
) -> str | None:
    """Write the asset id to a QuickTime movie file at filepath and save to destination path

    Args:
        filepath: Path to the QuickTime movie file.
        asset_id: The asset id to write to the file.

    Returns: Error message if there was an error, otherwise None.
    """
    filepath = str(filepath)
    with objc.autorelease_pool():
        # rename file so export can write to original path
        temp_filepath = f".{asset_id}_{filepath}"
        os.rename(filepath, temp_filepath)
        input_url = NSURL.fileURLWithPath_(str(temp_filepath))
        output_url = NSURL.fileURLWithPath_(str(filepath))
        asset = AVFoundation.AVAsset.assetWithURL_(input_url)
        metadata_item = avmetadata_for_asset_id(asset_id)
        export_session = (
            AVFoundation.AVAssetExportSession.alloc().initWithAsset_presetName_(
                asset, AVFoundation.AVAssetExportPresetPassthrough
            )
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


@click.command()
@click.argument("image", type=click.Path(exists=True, path_type=pathlib.Path))
@click.argument("video", type=click.Path(exists=True, path_type=pathlib.Path))
def main(image: pathlib.Path, video: pathlib.Path):
    if image.suffix.lower() not in [".jpg", ".jpeg", ".heic"]:
        click.echo(f"{image} is not a JPEG or HEIC image", err=True)
        raise click.Abort()
    if video.suffix.lower() not in [".mov", ".mp4"]:
        click.echo(f"{video} is not a QuickTime movie file", err=True)
        raise click.Abort()
    asset_id = str(uuid.uuid4()).upper()
    add_asset_id_to_image_file(image, asset_id)
    add_asset_id_to_quicktime_file(video, asset_id)


if __name__ == "__main__":
    main()
