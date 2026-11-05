"""Add the necessary metadata to photo + video pair so Photos recognizes them as Live Photos when imported"""

from __future__ import annotations

import os
import math
import pathlib
import shutil
import threading
import uuid

import AVFoundation
import cgmetadata
import CoreMedia
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
kKeyStillImageTime = "com.apple.quicktime.still-image-time"
kKeyStillTransform = "com.apple.quicktime.live-photo-still-image-transform"
kKeyStillDimensions = "com.apple.quicktime.live-photo-still-image-transform-reference-dimensions"


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
        metadata_as_mutable = metadata.mutableCopy() if metadata else NSMutableDictionary.alloc().init()
        maker_apple = metadata_as_mutable.objectForKey_(Quartz.kCGImagePropertyMakerAppleDictionary)
        if not maker_apple:
            maker_apple = NSMutableDictionary.alloc().init()
        else:
            maker_apple = maker_apple.mutableCopy()
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


def _add_asset_id_via_export_session(filepath: pathlib.Path, asset_id: str) -> str | None:
    """Stamp the content identifier via AVAssetExportSession (passthrough).

    This re-exports the file and does not preserve track reference atoms (tref).
    Used for non-QuickTime containers (e.g. .mp4) where AVMutableMovie's
    writeMovieHeaderToURL is not supported.
    """
    with objc.autorelease_pool():
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
                os.unlink(filepath)
            except FileNotFoundError:
                pass
            os.rename(temp_filepath, filepath)
        else:
            os.unlink(temp_filepath)

        return error or None


def add_asset_id_to_quicktime_file(filepath: str | os.PathLike, asset_id: str) -> str | None:
    """Write the asset id to a QuickTime movie file at filepath and save to destination path

    Args:
        filepath: Path to the QuickTime movie file.
        asset_id: The asset id to write to the file.

    Returns: Error message if there was an error, otherwise None.
    """
    filepath = pathlib.Path(filepath)
    if filepath.suffix.lower() != ".mov":
        return _add_asset_id_via_export_session(filepath, asset_id)

    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(str(filepath))
        movie, error = AVFoundation.AVMutableMovie.movieWithURL_options_error_(url, None, None)
        if movie is None:
            return f"Could not open {filepath} as AVMutableMovie: {error.description() if error else 'unknown error'}"

        existing = [
            item
            for item in (movie.metadata() or [])
            if not (str(item.key()) == kKeyContentIdentifier and str(item.keySpace()) == kKeySpaceQuickTimeMetadata)
        ]
        movie.setMetadata_(existing + [avmetadata_for_asset_id(asset_id)])

        success, error = movie.writeMovieHeaderToURL_fileType_options_error_(
            url, AVFoundation.AVFileTypeQuickTimeMovie, 0, None
        )
        if not success:
            return f"writeMovieHeaderToURL failed for {filepath}: {error.description() if error else 'unknown error'}"
        return None


### Live Wallpaper Generation (intoLive pipeline) ###


def _read_reference_metadata(reference_video_path):
    """Read opaque capture records for an explicitly requested structural experiment."""
    reference = AVFoundation.AVAsset.assetWithURL_(NSURL.fileURLWithPath_(str(reference_video_path)))
    duration = CoreMedia.CMTimeGetSeconds(reference.duration())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Reference movie must have a finite, positive duration")
    for track in reference.tracksWithMediaType_(AVFoundation.AVMediaTypeMetadata):
        descriptions = track.formatDescriptions()
        if not descriptions or "mdta/com.apple.quicktime.live-photo-info" not in (
            CoreMedia.CMMetadataFormatDescriptionGetIdentifiers(descriptions[0]) or []
        ):
            continue
        reader, error = AVFoundation.AVAssetReader.assetReaderWithAsset_error_(reference, None)
        if reader is None:
            raise RuntimeError(f"Cannot read reference metadata: {error}")
        output = AVFoundation.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(track, None)
        reader.addOutput_(output)
        adaptor = AVFoundation.AVAssetReaderOutputMetadataAdaptor.assetReaderOutputMetadataAdaptorWithAssetReaderTrackOutput_(output)
        if not reader.startReading():
            raise RuntimeError(f"Cannot read reference metadata: {reader.error()}")
        groups = []
        while (group := adaptor.nextTimedMetadataGroup()) is not None:
            groups.append(group)
        if reader.status() == AVFoundation.AVAssetReaderStatusFailed:
            raise RuntimeError(f"Cannot read reference metadata: {reader.error()}")
        reader.cancelReading()
        if not groups:
            raise ValueError("Reference live-photo-info track contains no records")
        vitality = [m for m in reference.metadata() if str(m.key()) in {
            "com.apple.quicktime.live-photo.vitality-score",
            "com.apple.quicktime.live-photo.vitality-scoring-version",
        }]
        return descriptions[0], groups, duration, vitality
    raise ValueError("Reference movie has no live-photo-info metadata track")


def _build_live_wallpaper_video(
    source_video_path: pathlib.Path,
    destination_video_path: pathlib.Path,
    asset_id: str,
    still_image_time_seconds: float = 0.0,
    duration_seconds: float = 1612 / 600,
    include_still_transform: bool = False,
    reference_video_path: pathlib.Path | None = None,
) -> None:
    """Remuxes or encodes source_video_path into a Live-Wallpaper-ready QuickTime movie.

    Creates:
      1. A video track trimmed to duration_seconds (default: 2.687 seconds).
      2. An audio track (if present) trimmed to the same range.
      3. A timed metadata track with 'mdta/com.apple.quicktime.still-image-time'.
      4. A 'cdsc' track association from the metadata track to the video track ('tref' atom).
      5. Top-level metadata with 'com.apple.quicktime.content.identifier'.
    """
    with objc.autorelease_pool():
        input_url = NSURL.fileURLWithPath_(str(source_video_path))
        output_url = NSURL.fileURLWithPath_(str(destination_video_path))

        reference_data = _read_reference_metadata(reference_video_path) if reference_video_path else None
        asset = AVFoundation.AVAsset.assetWithURL_(input_url)
        video_tracks = asset.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo)
        if not video_tracks:
            raise ValueError(f"No video track found in {source_video_path}")
        video_track = video_tracks[0]

        reader, err = AVFoundation.AVAssetReader.assetReaderWithAsset_error_(asset, None)
        if reader is None:
            raise RuntimeError(f"Could not create AVAssetReader: {err}")

        # Center the trim window on the selected key frame where possible.
        time_scale = video_track.naturalTimeScale() or 600
        asset_duration_cm = asset.duration()
        total_seconds = (
            asset_duration_cm.value / asset_duration_cm.timescale
            if asset_duration_cm.timescale > 0
            else duration_seconds
        )

        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("duration must be finite and greater than zero")
        if not math.isfinite(still_image_time_seconds) or not 0 <= still_image_time_seconds < total_seconds:
            raise ValueError("still_image_time must be within the source video")
        start_sec = max(0.0, min(still_image_time_seconds - duration_seconds / 2, max(0.0, total_seconds - duration_seconds)))
        actual_duration = min(duration_seconds, max(0.0, total_seconds - start_sec))

        start_cm = CoreMedia.CMTimeMake(int(start_sec * time_scale), time_scale)
        duration_cm = CoreMedia.CMTimeMake(int(actual_duration * time_scale), time_scale)
        reader.setTimeRange_(CoreMedia.CMTimeRangeMake(start_cm, duration_cm))

        # Check if the source video format descriptions are available for passthrough
        format_descs = video_track.formatDescriptions()
        can_passthrough = bool(format_descs) and CoreMedia.CMFormatDescriptionGetMediaSubType(format_descs[0]) == int.from_bytes(b"hvc1", "big")

        if can_passthrough:
            video_output = AVFoundation.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(
                video_track, None
            )
            video_input = (
                AVFoundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
                    AVFoundation.AVMediaTypeVideo, None, format_descs[0]
                )
            )
        else:
            # Encode non-HEVC sources to match the reference codec
            video_output = AVFoundation.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(
                video_track,
                {Quartz.kCVPixelBufferPixelFormatTypeKey: Quartz.kCVPixelFormatType_32BGRA},
            )
            natural_size = video_track.naturalSize()
            video_settings = {
                AVFoundation.AVVideoCodecKey: AVFoundation.AVVideoCodecTypeHEVC,
                AVFoundation.AVVideoWidthKey: int(natural_size.width),
                AVFoundation.AVVideoHeightKey: int(natural_size.height),
            }
            video_input = AVFoundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_(
                AVFoundation.AVMediaTypeVideo, video_settings
            )

        reader.addOutput_(video_output)
        video_input.setExpectsMediaDataInRealTime_(False)
        video_input.setTransform_(video_track.preferredTransform())
        video_input.setMetadata_(video_track.metadata() or [])

        # Audio track (if present)
        audio_tracks = asset.tracksWithMediaType_(AVFoundation.AVMediaTypeAudio)
        audio_output = None
        audio_input = None
        if audio_tracks and audio_tracks[0].formatDescriptions():
            audio_output = AVFoundation.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(
                audio_tracks[0], None
            )
            audio_input = (
                AVFoundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
                    AVFoundation.AVMediaTypeAudio, None, audio_tracks[0].formatDescriptions()[0]
                )
            )
            audio_input.setExpectsMediaDataInRealTime_(False)
            reader.addOutput_(audio_output)

        writer, err = AVFoundation.AVAssetWriter.assetWriterWithURL_fileType_error_(
            output_url, AVFoundation.AVFileTypeQuickTimeMovie, None
        )
        if writer is None:
            raise RuntimeError(f"Could not create AVAssetWriter: {err}")

        # Top-level Content Identifier metadata
        id_item = AVFoundation.AVMutableMetadataItem.metadataItem()
        id_item.setKey_(kKeyContentIdentifier)
        id_item.setKeySpace_(kKeySpaceQuickTimeMetadata)
        id_item.setValue_(asset_id)
        id_item.setDataType_("com.apple.metadata.datatype.UTF-8")
        metadata = [item for item in (asset.metadata() or [])
                    if str(item.key()) != kKeyContentIdentifier]
        if not any(str(item.key()) == "com.apple.quicktime.full-frame-rate-playback-intent" for item in metadata):
            intent = AVFoundation.AVMutableMetadataItem.metadataItem()
            intent.setKey_("com.apple.quicktime.full-frame-rate-playback-intent")
            intent.setKeySpace_(kKeySpaceQuickTimeMetadata)
            intent.setValue_(1)
            intent.setDataType_("com.apple.metadata.datatype.int64")
            metadata.append(intent)
        if reference_data:
            vitality = reference_data[3]
            replaced_keys = {str(item.key()) for item in vitality}
            metadata = [item for item in metadata if str(item.key()) not in replaced_keys] + vitality
        writer.setMetadata_(metadata + [id_item])
        writer.setMovieTimeScale_(600)

        writer.addInput_(video_input)
        if audio_input:
            writer.addInput_(audio_input)

        # Timed metadata track for still-image-time
        spec = {
            CoreMedia.kCMMetadataFormatDescriptionMetadataSpecificationKey_Identifier: f"mdta/{kKeyStillImageTime}",
            CoreMedia.kCMMetadataFormatDescriptionMetadataSpecificationKey_DataType: "com.apple.metadata.datatype.int8",
        }
        specs = [spec]
        if include_still_transform:
            for key, datatype in [
                (kKeyStillTransform, "com.apple.metadata.perspective-transform-float64"),
                (kKeyStillDimensions, "com.apple.metadata.datatype.dimensions-float32"),
            ]:
                specs.append({
                    CoreMedia.kCMMetadataFormatDescriptionMetadataSpecificationKey_Identifier: f"mdta/{key}",
                    CoreMedia.kCMMetadataFormatDescriptionMetadataSpecificationKey_DataType: datatype,
                })
        err, desc = CoreMedia.CMMetadataFormatDescriptionCreateWithMetadataSpecifications(
            None, CoreMedia.kCMMetadataFormatType_Boxed, specs, None
        )
        if desc is None:
            raise RuntimeError(f"Failed to create metadata format description: {err}")

        meta_input = (
            AVFoundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
                AVFoundation.AVMediaTypeMetadata, None, desc
            )
        )
        meta_adaptor = (
            AVFoundation.AVAssetWriterInputMetadataAdaptor.assetWriterInputMetadataAdaptorWithAssetWriterInput_(
                meta_input
            )
        )
        meta_input.setExpectsMediaDataInRealTime_(False)

        # Establish 'cdsc' track association ('tref' atom) from metadata input to video input
        meta_input.addTrackAssociationWithTrackOfInput_type_(
            video_input, AVFoundation.AVTrackAssociationTypeMetadataReferent
        )
        writer.addInput_(meta_input)

        # Keep source camera motion/live-photo-info tracks and their format setup data.
        auxiliary = []
        existing_orientation = False
        for track in asset.tracksWithMediaType_(AVFoundation.AVMediaTypeMetadata):
            descriptions = track.formatDescriptions()
            if not descriptions:
                continue
            identifiers = CoreMedia.CMMetadataFormatDescriptionGetIdentifiers(descriptions[0]) or []
            existing_orientation |= "mdta/com.apple.quicktime.video-orientation" in identifiers
            if reference_data and "mdta/com.apple.quicktime.live-photo-info" in identifiers:
                continue
            if f"mdta/{kKeyStillImageTime}" in identifiers:
                # This marker is regenerated at the requested key photo time.
                continue
            output = AVFoundation.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(track, None)
            input_ = AVFoundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
                AVFoundation.AVMediaTypeMetadata, None, descriptions[0])
            input_.addTrackAssociationWithTrackOfInput_type_(video_input, AVFoundation.AVTrackAssociationTypeMetadataReferent)
            reader.addOutput_(output)
            writer.addInput_(input_)
            auxiliary.append((output, input_))

        experimental_tracks = []
        if reference_data:
            description, groups, reference_duration, _ = reference_data
            reference_input = AVFoundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
                AVFoundation.AVMediaTypeMetadata, None, description)
            reference_input.addTrackAssociationWithTrackOfInput_type_(video_input, AVFoundation.AVTrackAssociationTypeMetadataReferent)
            reference_adaptor = AVFoundation.AVAssetWriterInputMetadataAdaptor.assetWriterInputMetadataAdaptorWithAssetWriterInput_(reference_input)
            writer.addInput_(reference_input)
            retimed = []
            for group in groups:
                old = group.timeRange()
                begin = max(0.0, min(actual_duration, CoreMedia.CMTimeGetSeconds(old.start) / reference_duration * actual_duration))
                end = max(begin, min(actual_duration, CoreMedia.CMTimeGetSeconds(CoreMedia.CMTimeRangeGetEnd(old)) / reference_duration * actual_duration))
                begin_cm = CoreMedia.CMTimeMakeWithSeconds(begin, 600)
                end_cm = CoreMedia.CMTimeMakeWithSeconds(end, 600)
                if CoreMedia.CMTimeCompare(end_cm, begin_cm) <= 0:
                    continue
                interval = CoreMedia.CMTimeRangeMake(CoreMedia.CMTimeAdd(start_cm, begin_cm), CoreMedia.CMTimeSubtract(end_cm, begin_cm))
                retimed.append(AVFoundation.AVTimedMetadataGroup.alloc().initWithItems_timeRange_(group.items(), interval))
            experimental_tracks.append((reference_input, reference_adaptor, retimed))

            if not existing_orientation:
                key = "com.apple.quicktime.video-orientation"
                spec = {
                    CoreMedia.kCMMetadataFormatDescriptionMetadataSpecificationKey_Identifier: f"mdta/{key}",
                    CoreMedia.kCMMetadataFormatDescriptionMetadataSpecificationKey_DataType: "com.apple.metadata.datatype.int16",
                }
                status, description = CoreMedia.CMMetadataFormatDescriptionCreateWithMetadataSpecifications(
                    None, CoreMedia.kCMMetadataFormatType_Boxed, [spec], None)
                if description is None:
                    raise RuntimeError(f"Cannot create orientation format: {status}")
                orientation_input = AVFoundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
                    AVFoundation.AVMediaTypeMetadata, None, description)
                orientation_input.addTrackAssociationWithTrackOfInput_type_(video_input, AVFoundation.AVTrackAssociationTypeMetadataReferent)
                orientation_adaptor = AVFoundation.AVAssetWriterInputMetadataAdaptor.assetWriterInputMetadataAdaptorWithAssetWriterInput_(orientation_input)
                writer.addInput_(orientation_input)
                transform = video_track.preferredTransform()
                matrix = tuple(round(x) for x in (transform.a, transform.b, transform.c, transform.d))
                orientations = {(1, 0, 0, 1): 1, (-1, 0, 0, 1): 2, (-1, 0, 0, -1): 3,
                                (1, 0, 0, -1): 4, (0, 1, 1, 0): 5, (0, 1, -1, 0): 6,
                                (0, -1, -1, 0): 7, (0, -1, 1, 0): 8}
                item = AVFoundation.AVMutableMetadataItem.metadataItem()
                item.setKey_(key)
                item.setKeySpace_(kKeySpaceQuickTimeMetadata)
                item.setDataType_("com.apple.metadata.datatype.int16")
                item.setValue_(orientations.get(matrix, 1))
                group = AVFoundation.AVTimedMetadataGroup.alloc().initWithItems_timeRange_(
                    [item], CoreMedia.CMTimeRangeMake(start_cm, duration_cm))
                experimental_tracks.append((orientation_input, orientation_adaptor, [group]))

        if not reader.startReading():
            raise RuntimeError(f"AVAssetReader failed to start reading: {reader.error()}")
        if not writer.startWriting():
            raise RuntimeError(f"AVAssetWriter failed to start writing: {writer.error()}")

        writer.startSessionAtSourceTime_(start_cm)

        # Append still-image-time timed metadata item
        still_item = AVFoundation.AVMutableMetadataItem.metadataItem()
        still_item.setKey_(kKeyStillImageTime)
        still_item.setKeySpace_(kKeySpaceQuickTimeMetadata)
        still_item.setValue_(-1)
        still_item.setDataType_("com.apple.metadata.datatype.int8")

        time_range = CoreMedia.CMTimeRangeMake(
            CoreMedia.CMTimeMakeWithSeconds(still_image_time_seconds, time_scale),
            CoreMedia.CMTimeMake(1, 600))
        still_items = [still_item]
        if include_still_transform:
            # The photo comes from this video frame, so no stabilization offset is needed.
            transform = AVFoundation.AVMutableMetadataItem.metadataItem()
            transform.setKey_(kKeyStillTransform)
            transform.setKeySpace_(kKeySpaceQuickTimeMetadata)
            transform.setDataType_("com.apple.metadata.perspective-transform-float64")
            transform.setValue_([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
            dimensions = AVFoundation.AVMutableMetadataItem.metadataItem()
            dimensions.setKey_(kKeyStillDimensions)
            dimensions.setKeySpace_(kKeySpaceQuickTimeMetadata)
            dimensions.setDataType_("com.apple.metadata.datatype.dimensions-float32")
            from Foundation import NSValue
            dimensions.setValue_(NSValue.valueWithSize_(video_track.naturalSize()))
            still_items.extend([transform, dimensions])
        group = AVFoundation.AVTimedMetadataGroup.alloc().initWithItems_timeRange_(still_items, time_range)
        if not meta_adaptor.appendTimedMetadataGroup_(group):
            raise RuntimeError(f"Could not append key photo metadata: {writer.error()}")
        meta_input.markAsFinished()

        # Drain tracks concurrently: encoding can block if another track is starved.
        failures = []

        def copy_samples(output, input_):
            try:
                while True:
                    sample = output.copyNextSampleBuffer()
                    if sample is None:
                        break
                    while not input_.isReadyForMoreMediaData():
                        if writer.status() != AVFoundation.AVAssetWriterStatusWriting:
                            raise RuntimeError(f"Writer stopped: {writer.error()}")
                        threading.Event().wait(0.005)
                    if not input_.appendSampleBuffer_(sample):
                        raise RuntimeError(f"Could not append sample: {writer.error()}")
            except Exception as error:
                failures.append(error)
                writer.cancelWriting()
            finally:
                input_.markAsFinished()

        def copy_metadata(input_, adaptor, groups):
            try:
                for group in groups:
                    while not input_.isReadyForMoreMediaData():
                        if writer.status() != AVFoundation.AVAssetWriterStatusWriting:
                            raise RuntimeError(f"Writer stopped: {writer.error()}")
                        threading.Event().wait(0.005)
                    if not adaptor.appendTimedMetadataGroup_(group):
                        raise RuntimeError(f"Cannot append reference metadata: {writer.error()}")
            except Exception as error:
                failures.append(error)
                writer.cancelWriting()
            finally:
                input_.markAsFinished()

        tracks = [(video_output, video_input)] + auxiliary
        if audio_output is not None and audio_input is not None:
            tracks.append((audio_output, audio_input))
        workers = [threading.Thread(target=copy_samples, args=track) for track in tracks]
        workers.extend(threading.Thread(target=copy_metadata, args=track) for track in experimental_tracks)
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        if failures:
            reader.cancelReading()
            raise failures[0]
        if reader.status() == AVFoundation.AVAssetReaderStatusFailed:
            writer.cancelWriting()
            raise RuntimeError(f"AVAssetReader failed: {reader.error()}")
        writer.endSessionAtSourceTime_(CoreMedia.CMTimeAdd(start_cm, duration_cm))

        done_event = threading.Event()
        writer.finishWritingWithCompletionHandler_(lambda: done_event.set())
        done_event.wait()

        if writer.status() != AVFoundation.AVAssetWriterStatusCompleted:
            err = writer.error()
            raise RuntimeError(f"AVAssetWriter failed: {err.description() if err else 'unknown'}")


def extract_key_photo_from_video(
    video_path: str | os.PathLike,
    output_image_path: str | os.PathLike,
    asset_id: str,
    time_seconds: float = 0.0,
) -> pathlib.Path:
    """Extract a frame from video at time_seconds and save it as HEIC/JPEG with the asset_id metadata.

    Args:
        video_path: Path to the video file.
        output_image_path: Path to write the still image (HEIC or JPEG).
        asset_id: The asset id to write to the MakerApple dictionary.
        time_seconds: Timestamp in seconds to extract the key frame.

    Returns:
        pathlib.Path to the created image.
    """
    video_path = pathlib.Path(video_path)
    output_image_path = pathlib.Path(output_image_path)

    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(str(video_path))
        asset = AVFoundation.AVAsset.assetWithURL_(url)
        generator = AVFoundation.AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
        generator.setAppliesPreferredTrackTransform_(True)

        # Precise time matching
        generator.setRequestedTimeToleranceBefore_(CoreMedia.kCMTimeZero)
        generator.setRequestedTimeToleranceAfter_(CoreMedia.kCMTimeZero)

        track = asset.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo)[0]
        target_time = CoreMedia.CMTimeMakeWithSeconds(time_seconds, track.naturalTimeScale() or 600)
        cg_image, _actual_time = generator.copyCGImageAtTime_actualTime_error_(target_time, None, None)

        if not cg_image:
            raise ValueError(f"Could not extract frame from {video_path} at time {time_seconds}")

        is_heic = output_image_path.suffix.lower() in [".heic", ".heif"]
        uti_type = "public.heic" if is_heic else "public.jpeg"

        dest_url = NSURL.fileURLWithPath_(str(output_image_path))
        dest = Quartz.CGImageDestinationCreateWithURL(dest_url, uti_type, 1, None)
        if not dest:
            raise ValueError(f"Could not create image destination for {output_image_path}")

        maker_apple = NSMutableDictionary.alloc().init()
        maker_apple.setObject_forKey_(asset_id, kFigAppleMakerNote_AssetIdentifier)

        props = NSMutableDictionary.alloc().init()
        props.setObject_forKey_(maker_apple, Quartz.kCGImagePropertyMakerAppleDictionary)

        Quartz.CGImageDestinationAddImage(dest, cg_image, props)
        if not Quartz.CGImageDestinationFinalize(dest):
            raise RuntimeError(f"Failed to finalize image at {output_image_path}")

        return output_image_path


def is_image_file(filepath: str | os.PathLike) -> bool:
    """Return True if the file is a JPEG or HEIC image file"""
    filepath = pathlib.Path(filepath)
    return filepath.suffix.lower() in [".jpg", ".jpeg", ".heic", ".heif"]


def is_video_file(filepath: str | os.PathLike) -> bool:
    """Return True if the file is a MOV or MP4 video file"""
    filepath = pathlib.Path(filepath)
    return filepath.suffix.lower() in [".mov", ".mp4"]


### Public API ###


def make_live_photo(
    image_path: str | os.PathLike,
    video_path: str | os.PathLike,
    asset_id: str | None = None,
) -> str:
    """Given a JPEG/HEIC image and a QuickTime video, add the necessary metadata to make it a Live Photo.

    Args:
        image_path: Path to the image file.
        video_path: Path to the QuickTime movie file.
        asset_id: The asset id to write to the file; if not provided a unique asset will be created.

    Returns: The asset id (content identifier) written to the photo + video pair.
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


def _nearest_video_frame_time(asset, requested_time: float) -> float:
    """Choose an actual presentation timestamp, including for variable-frame-rate input."""
    track = asset.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo)[0]
    reader, error = AVFoundation.AVAssetReader.assetReaderWithAsset_error_(asset, None)
    if reader is None:
        raise RuntimeError(f"Could not read key frame timing: {error}")
    output = AVFoundation.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(track, None)
    reader.addOutput_(output)
    if not reader.startReading():
        raise RuntimeError(f"Could not read key frame timing: {reader.error()}")
    nearest = None
    try:
        while True:
            sample = output.copyNextSampleBuffer()
            if sample is None:
                break
            seconds = CoreMedia.CMTimeGetSeconds(CoreMedia.CMSampleBufferGetPresentationTimeStamp(sample))
            if math.isfinite(seconds) and (nearest is None or abs(seconds - requested_time) < abs(nearest - requested_time)):
                nearest = seconds
            # Compressed samples may arrive in decode order. Read the entire range
            # rather than assuming monotonically increasing presentation timestamps.
        if reader.status() == AVFoundation.AVAssetReaderStatusFailed:
            raise RuntimeError(f"Could not read key frame timing: {reader.error()}")
    finally:
        reader.cancelReading()
    if nearest is None:
        raise ValueError("Video has no readable frame timestamps")
    return nearest


def _make_live_wallpaper_pair(
    video_path: str | os.PathLike,
    image_path: str | os.PathLike | None = None,
    asset_id: str | None = None,
    still_image_time: float | None = None,
    duration: float = 1612 / 600,
    output_dir: str | os.PathLike | None = None,
    reference_video: str | os.PathLike | None = None,
) -> tuple[str, pathlib.Path, pathlib.Path]:
    """Create a wallpaper candidate without modifying the source image or movie.

    Output goes to a new <video stem>_wallpaper directory, or output_dir. Existing
    output directories are rejected. The default key photo is an actual frame near
    the midpoint of the first duration seconds. Explicit still_image_time is a
    source-video timestamp. iPhone wallpaper eligibility requires device testing.

    reference_video explicitly enables experimental transplantation of opaque
    live-photo-info records and vitality scores. Records are retimed, not computed
    for the source; their payloads and setup data still describe the reference.
    """
    video_path = pathlib.Path(video_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"{video_path} does not exist")
    if not is_video_file(video_path):
        raise ValueError(f"{video_path} is not a video file (.mov or .mp4)")
    if image_path is not None:
        image_path = pathlib.Path(image_path).resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"{image_path} does not exist")
        if not is_image_file(image_path):
            raise ValueError(f"{image_path} is not a JPEG or HEIC image")
    if reference_video is not None:
        reference_video = pathlib.Path(reference_video).resolve()
        if not reference_video.is_file():
            raise FileNotFoundError(f"{reference_video} does not exist")
        _read_reference_metadata(reference_video)  # Validate before creating output.
    asset_id = asset_id or str(uuid.uuid4()).upper()
    with objc.autorelease_pool():
        source = AVFoundation.AVAsset.assetWithURL_(NSURL.fileURLWithPath_(str(video_path)))
        if not source.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo):
            raise ValueError(f"No video track found in {video_path}")
        source_duration = CoreMedia.CMTimeGetSeconds(source.duration())
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise ValueError("Source video must have a finite, positive duration")
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("duration must be finite and greater than zero")
        if still_image_time is None:
            still_image_time = min(duration, source_duration) / 2
        if not math.isfinite(still_image_time) or not 0 <= still_image_time < source_duration:
            raise ValueError("still_image_time must be within the source video")
        still_image_time = _nearest_video_frame_time(source, still_image_time)

    output_dir = pathlib.Path(output_dir).resolve() if output_dir else video_path.parent / f"{video_path.stem}_wallpaper"
    output_dir.mkdir()  # Refuse to overwrite any previous output or input.
    target_video = output_dir / f"{video_path.stem}.mov"
    target_image = output_dir / f"{video_path.stem}{image_path.suffix if image_path else '.heic'}"
    try:
        _build_live_wallpaper_video(
            source_video_path=video_path,
            destination_video_path=target_video,
            asset_id=asset_id,
            still_image_time_seconds=still_image_time,
            duration_seconds=duration,
            include_still_transform=image_path is None,
            reference_video_path=reference_video,
        )
        if image_path is not None:
            shutil.copyfile(image_path, target_image)
            add_asset_id_to_image_file(target_image, asset_id)
        else:
            # Decode the final encoded movie, so the still matches its HEVC frame.
            start = max(0.0, min(still_image_time - duration / 2, max(0.0, source_duration - duration)))
            track = source.tracksWithMediaType_(AVFoundation.AVMediaTypeVideo)[0]
            scale = track.naturalTimeScale() or 600
            start = int(start * scale) / scale
            extract_key_photo_from_video(target_video, target_image, asset_id, still_image_time - start)
    except Exception:
        shutil.rmtree(output_dir)
        raise
    return asset_id, target_image, target_video


def make_live_wallpaper(
    video_path, image_path=None, asset_id=None, still_image_time=None, duration=1612 / 600,
    output_dir=None, reference_video=None, resolution="1080x1920", quality="high",
):
    """Create a wallpaper pair through the phone-tested encoder; preserve inputs."""
    from .wallpaper import make_live_wallpaper as convert
    return convert(video_path, image_path, asset_id, still_image_time, duration, output_dir,
                   reference_video, resolution, quality)


def save_live_photo_pair_as_pvt(
    image_path: str | os.PathLike,
    video_path: str | os.PathLike,
    pvt_path: str | os.PathLike | None = None,
    asset_id: str | None = None,
) -> tuple[str, pathlib.Path]:
    """Given a JPEG/HEIC image and a QuickTime video, add the necessary metadata to make it a Live Photo
    and package as a .pvt package which can be double-clicked to import into Photos as a Live Photo.
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

    if not is_live_photo_pair(image_path, video_path) or asset_id is not None:
        asset_id = make_live_photo(image_path, video_path, asset_id)
    else:
        asset_id = live_id(image_path)

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
    """Returns the Live Photo Content Identifier for the file or None"""
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
    """Check if the image and video pair are a Live Photo"""
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
