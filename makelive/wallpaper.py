"""Experimental wallpaper encoder promoted from the phone-tested quality matrix.

Only the wallpaper API/CLI uses this module. No experiment scripts or external
executables are needed at runtime. Reference info is borrowed, not recomputed.
"""

from __future__ import annotations

import json
import math
import pathlib
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace

import AVFoundation as AV
import CoreMedia as CM
import Quartz as Q
from Foundation import NSURL

from .makelive import _make_live_wallpaper_pair, _read_reference_metadata, is_image_file, is_video_file

DATA = pathlib.Path(__file__).with_name("data")
RESOLUTIONS = {"1080x1440": (1080, 1440), "1080x1920": (1080, 1920)}
QUALITIES = {"high": (30_000_000, 0.95), "lower": (4_000_000, 0.60)}


def asset(path):
    return AV.AVAsset.assetWithURL_(NSURL.fileURLWithPath_(str(path.resolve())))


def video_timestamps(movie):
    track = movie.tracksWithMediaType_(AV.AVMediaTypeVideo)[0]
    reader, error = AV.AVAssetReader.assetReaderWithAsset_error_(movie, None)
    if reader is None:
        raise RuntimeError(str(error))
    output = AV.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(track, None)
    reader.addOutput_(output)
    if not reader.startReading():
        raise RuntimeError(str(reader.error()))
    stamps = []
    while (sample := output.copyNextSampleBuffer()) is not None:
        if CM.CMSampleBufferGetNumSamples(sample) > 0 and CM.CMSampleBufferGetTotalSampleSize(sample) > 0:
            stamp = CM.CMSampleBufferGetPresentationTimeStamp(sample)
            # AVAssetReader exposes media timestamps for movie tracks edited in
            # place. Convert through their edit list to movie presentation time.
            for segment in track.segments():
                if segment.isEmpty():
                    continue
                mapping = segment.timeMapping()
                if (
                    CM.CMTimeCompare(stamp, mapping.source.start) >= 0
                    and CM.CMTimeCompare(stamp, CM.CMTimeRangeGetEnd(mapping.source)) < 0
                ):
                    stamp = CM.CMTimeMapTimeFromRangeToRange(stamp, mapping.source, mapping.target)
                    stamps.append(stamp)
                    break
    if reader.status() == AV.AVAssetReaderStatusFailed:
        raise RuntimeError(str(reader.error()))
    reader.cancelReading()
    return sorted(stamps, key=CM.CMTimeGetSeconds)


def fit_frame_image(image, orientation, size):
    """Use the same orientation/crop for original and blended source frames."""
    ci = Q.CIImage.imageWithCGImage_(image).imageByApplyingOrientation_(orientation)
    extent = ci.extent()
    ci = ci.imageByApplyingTransform_(Q.CGAffineTransformMakeTranslation(-extent.origin.x, -extent.origin.y))
    scale = max(size.width / extent.size.width, size.height / extent.size.height)
    ci = ci.imageByApplyingTransform_(Q.CGAffineTransformMakeScale(scale, scale))
    return ci.imageByApplyingTransform_(
        Q.CGAffineTransformMakeTranslation(
            (size.width - extent.size.width * scale) / 2, (size.height - extent.size.height * scale) / 2
        )
    )


def _reference_profile(reference):
    if reference is None:
        return DATA / "wallpaper_metadata.mov", json.loads((DATA / "wallpaper_profile.json").read_text())
    reference = pathlib.Path(reference).resolve()
    if not reference.is_file():
        raise FileNotFoundError(reference)
    _read_reference_metadata(reference)
    movie = asset(reference)
    if not movie.tracksWithMediaType_(AV.AVMediaTypeVideo):
        raise ValueError("Reference video must have a video track")
    duration = CM.CMTimeGetSeconds(movie.duration())
    points = video_timestamps(movie)
    if not points or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Reference video has no finite video timeline")
    marker = duration / 2
    for track in movie.tracksWithMediaType_(AV.AVMediaTypeMetadata):
        if not any(
            "mdta/com.apple.quicktime.still-image-time" in (CM.CMMetadataFormatDescriptionGetIdentifiers(d) or [])
            for d in track.formatDescriptions()
        ):
            continue
        reader, error = AV.AVAssetReader.assetReaderWithAsset_error_(movie, None)
        if reader is None:
            raise RuntimeError(str(error))
        output = AV.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(track, None)
        reader.addOutput_(output)
        adaptor = AV.AVAssetReaderOutputMetadataAdaptor.assetReaderOutputMetadataAdaptorWithAssetReaderTrackOutput_(output)
        if not reader.startReading():
            raise RuntimeError(str(reader.error()))
        group = adaptor.nextTimedMetadataGroup()
        if group is not None:
            marker = CM.CMTimeGetSeconds(group.timeRange().start)
        reader.cancelReading()
        break
    profile = json.loads((DATA / "wallpaper_profile.json").read_text())
    profile.update(duration=duration, marker_time=marker, frame_times=[CM.CMTimeGetSeconds(p) for p in points])
    return reference, profile


def _encode(source, destination, points, stamps, duration, dimensions, bitrate, colors):
    width, height = dimensions
    settings = {
        AV.AVVideoCodecKey: AV.AVVideoCodecTypeHEVC,
        AV.AVVideoWidthKey: width,
        AV.AVVideoHeightKey: height,
        AV.AVVideoCompressionPropertiesKey: {
            AV.AVVideoMaxKeyFrameIntervalKey: 1,
            AV.AVVideoAllowFrameReorderingKey: False,
            AV.AVVideoAverageBitRateKey: bitrate,
        },
        AV.AVVideoColorPropertiesKey: {
            AV.AVVideoColorPrimariesKey: colors["primaries"],
            AV.AVVideoTransferFunctionKey: colors["transfer"],
            AV.AVVideoYCbCrMatrixKey: colors["matrix"],
        },
    }
    writer, error = AV.AVAssetWriter.assetWriterWithURL_fileType_error_(
        NSURL.fileURLWithPath_(str(destination)), AV.AVFileTypeQuickTimeMovie, None
    )
    if writer is None:
        raise RuntimeError(str(error))
    input_ = AV.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_(AV.AVMediaTypeVideo, settings)
    input_.setMediaTimeScale_(600)
    attrs = {
        Q.kCVPixelBufferPixelFormatTypeKey: Q.kCVPixelFormatType_32BGRA,
        Q.kCVPixelBufferWidthKey: width,
        Q.kCVPixelBufferHeightKey: height,
        Q.kCVPixelBufferIOSurfacePropertiesKey: {},
    }
    adaptor = AV.AVAssetWriterInputPixelBufferAdaptor.assetWriterInputPixelBufferAdaptorWithAssetWriterInput_sourcePixelBufferAttributes_(
        input_, attrs
    )
    writer.addInput_(input_)
    writer.setMovieTimeScale_(600)
    if not writer.startWriting():
        raise RuntimeError(str(writer.error()))
    writer.startSessionAtSourceTime_(CM.kCMTimeZero)
    generator = AV.AVAssetImageGenerator.assetImageGeneratorWithAsset_(source)
    generator.setAppliesPreferredTrackTransform_(True)
    generator.setApertureMode_(AV.AVAssetImageGeneratorApertureModeEncodedPixels)
    generator.setRequestedTimeToleranceBefore_(CM.kCMTimeZero)
    generator.setRequestedTimeToleranceAfter_(CM.kCMTimeZero)
    context = Q.CIContext.contextWithOptions_(None)
    try:
        for source_time, output_time in zip(points, stamps):
            frame, error = generator.copyCGImageAtTime_actualTime_error_(source_time, None, None)
            if frame is None:
                raise RuntimeError(f"Cannot decode source frame: {error}")
            ci = fit_frame_image(frame, 1, SimpleNamespace(width=width, height=height))
            status, buffer = Q.CVPixelBufferCreate(None, width, height, Q.kCVPixelFormatType_32BGRA, attrs, None)
            if status or buffer is None:
                raise RuntimeError(f"Cannot allocate output frame: {status}")
            context.render_toCVPixelBuffer_(ci, buffer)
            deadline = time.monotonic() + 30
            while not input_.isReadyForMoreMediaData():
                if writer.status() != AV.AVAssetWriterStatusWriting or time.monotonic() > deadline:
                    raise RuntimeError(f"Video encoder stopped: {writer.error()}")
                time.sleep(0.005)
            if not adaptor.appendPixelBuffer_withPresentationTime_(buffer, output_time):
                raise RuntimeError(f"Cannot encode frame: {writer.error()}")
        input_.markAsFinished()
        writer.endSessionAtSourceTime_(CM.CMTimeMakeWithSeconds(duration, 600))
        complete = threading.Event()
        writer.finishWritingWithCompletionHandler_(complete.set)
        if not complete.wait(60) or writer.status() != AV.AVAssetWriterStatusCompleted:
            raise RuntimeError(f"Encoding failed: {writer.error()}")
    except Exception:
        writer.cancelWriting()
        raise


def _write_still(movie, destination, profile_image, identifier, seconds, quality):
    source = asset(movie)
    generator = AV.AVAssetImageGenerator.assetImageGeneratorWithAsset_(source)
    generator.setAppliesPreferredTrackTransform_(True)
    generator.setRequestedTimeToleranceBefore_(CM.kCMTimeZero)
    generator.setRequestedTimeToleranceAfter_(CM.kCMTimeZero)
    track = source.tracksWithMediaType_(AV.AVMediaTypeVideo)[0]
    frame, error = generator.copyCGImageAtTime_actualTime_error_(
        CM.CMTimeMakeWithSeconds(seconds, track.naturalTimeScale()), None, None
    )
    if frame is None:
        raise RuntimeError(f"Cannot extract key photo: {error}")
    colorspace = Q.CGColorSpaceCreateWithName(Q.kCGColorSpaceSRGB)
    if profile_image is not None:
        image_source = Q.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(profile_image)), None)
        original = Q.CGImageSourceCreateImageAtIndex(image_source, 0, None) if image_source else None
        if original is None:
            raise ValueError(f"Cannot decode image color profile: {profile_image}")
        colorspace = Q.CGImageGetColorSpace(original) or colorspace
    ci = Q.CIImage.imageWithCGImage_(frame)
    converted = Q.CIContext.contextWithOptions_(None).createCGImage_fromRect_format_colorSpace_(
        ci, ci.extent(), Q.kCIFormatRGBA8, colorspace
    )
    if converted is None:
        raise RuntimeError("Cannot convert key photo color space")
    output = Q.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(str(destination)), "public.heic", 1, None)
    if output is None:
        raise RuntimeError("Cannot create key photo")
    Q.CGImageDestinationAddImage(
        output,
        converted,
        {
            Q.kCGImagePropertyMakerAppleDictionary: {"17": identifier},
            Q.kCGImageDestinationLossyCompressionQuality: quality,
        },
    )
    if not Q.CGImageDestinationFinalize(output):
        raise RuntimeError("Cannot finalize key photo")


def make_live_wallpaper(
    video_path,
    image_path=None,
    asset_id=None,
    still_image_time=None,
    duration=1612 / 600,
    output_dir=None,
    reference_video=None,
    resolution="1080x1920",
    quality="high",
):
    """Create one source-preserving pair using the phone-tested matrix pipeline.

    A supplied image supplies its color space; the actual still is extracted from
    the encoded marker frame. Reference metadata remains experimentally borrowed.
    """
    source_path = pathlib.Path(video_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not is_video_file(source_path):
        raise ValueError("Source must be a MOV or MP4 video")
    image_path = pathlib.Path(image_path).resolve() if image_path is not None else None
    if image_path is not None:
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        if not is_image_file(image_path):
            raise ValueError("Image must be JPEG or HEIC")
    if resolution not in RESOLUTIONS or quality not in QUALITIES:
        raise ValueError("Unsupported wallpaper resolution or quality preset")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and greater than zero")
    source = asset(source_path)
    if not source.tracksWithMediaType_(AV.AVMediaTypeVideo):
        raise ValueError("Source has no video track")
    total = CM.CMTimeGetSeconds(source.duration())
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Source must have a finite positive duration")
    if still_image_time is not None and (not math.isfinite(still_image_time) or not 0 <= still_image_time < total):
        raise ValueError("still_image_time must be within the source video")
    metadata_path, profile = _reference_profile(reference_video)
    _read_reference_metadata(metadata_path)  # Fail before writing any output if missing/invalid.
    actual_duration = min(duration, total)
    if actual_duration < 0.1:
        raise ValueError("Wallpaper duration must be at least 0.1 seconds")
    start = (
        max(0.0, min(still_image_time - actual_duration / 2, total - actual_duration)) if still_image_time is not None else 0.0
    )
    marker = (
        still_image_time - start
        if still_image_time is not None
        else profile["marker_time"] * actual_duration / profile["duration"]
    )
    # Use the tested 600-tick schedule, shared by video and still metadata.
    ticks = sorted({round(t / profile["duration"] * actual_duration * 600) for t in profile["frame_times"]})
    marker_tick = min(round(marker * 600), round(actual_duration * 600) - 1)
    ticks[min(range(len(ticks)), key=lambda n: abs(ticks[n] - marker_tick))] = marker_tick
    ticks = sorted(set(ticks))
    marker = marker_tick / 600
    stamps = [CM.CMTimeMake(t, 600) for t in ticks]
    available = video_timestamps(source)
    if not available:
        raise ValueError("Source has no readable video frames")
    points = [min(available, key=lambda point: abs(CM.CMTimeGetSeconds(point) - (start + t / 600))) for t in ticks]
    destination = pathlib.Path(output_dir).resolve() if output_dir else source_path.parent / f"{source_path.stem}_wallpaper"
    if destination.exists():
        raise FileExistsError(f"Output directory already exists: {destination}")
    identifier = asset_id or str(uuid.uuid4()).upper()
    rate, photo_quality = QUALITIES[quality]
    dimensions = RESOLUTIONS[resolution]
    bitrate = round(rate * dimensions[0] * dimensions[1] / (1080 * 1920))
    with tempfile.TemporaryDirectory(prefix=".makelive-wallpaper-", dir=destination.parent) as temporary:
        work = pathlib.Path(temporary)
        encoded = work / f"{source_path.stem}.mov"
        _encode(source, encoded, points, stamps, actual_duration, dimensions, bitrate, profile["colors"])
        _, staged_image, staged_movie = _make_live_wallpaper_pair(
            encoded,
            asset_id=identifier,
            duration=actual_duration,
            still_image_time=marker,
            output_dir=work / "pair",
            reference_video=metadata_path,
        )
        expected_marker = CM.CMTimeMake(marker_tick, 600)
        if not any(CM.CMTimeCompare(point, expected_marker) == 0 for point in video_timestamps(asset(staged_movie))):
            raise RuntimeError("Remuxed video has no frame at the key-photo marker")
        final_marker = marker
        still = work / "matched.heic"
        _write_still(staged_movie, still, image_path, identifier, final_marker, photo_quality)
        still.replace(staged_image)
        (work / "pair").rename(destination)
    return identifier, destination / staged_image.name, destination / staged_movie.name
