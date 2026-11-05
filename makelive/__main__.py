"""Command line interface for makelive."""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterable

import click

from .makelive import (
    is_image_file,
    is_live_photo_pair,
    is_video_file,
    make_live_photo,
    make_live_wallpaper,
    save_live_photo_pair_as_pvt,
)
from .version import __version__


def find_photo_video_pairs(
    file_paths: Iterable[str | os.PathLike],
) -> tuple[list[tuple[pathlib.Path, pathlib.Path]], list[pathlib.Path]]:
    """Find photo and video pairs in a list of file paths."""
    matched_files, unmatched_files, image_files, video_files = [], [], {}, {}

    for fp in file_paths:
        file_path = pathlib.Path(fp)
        file_stem = file_path.stem
        if is_image_file(file_path):
            image_files[file_stem] = file_path.resolve()
        elif is_video_file(file_path):
            video_files[file_stem] = file_path.resolve()

    for key, image_file in image_files.items():
        if key in video_files:
            matched_files.append((image_file, video_files[key]))
            del video_files[key]
        else:
            unmatched_files.append(image_file)

    unmatched_files.extend(video_files.values())

    return matched_files, unmatched_files


def check_pair(image: pathlib.Path, video: pathlib.Path):
    """Check if a photo and video pair is a Live Photo."""
    if check_id := is_live_photo_pair(image, video):
        click.echo(f"{image} and {video} are Live Photos: {check_id}")
    else:
        click.echo(f"{image} and {video} are not Live Photos")


@click.command()
@click.version_option(version=__version__)
@click.option(
    "-c",
    "--check",
    is_flag=True,
    help="Check if file pair is a Live Photo but do not modify it",
)
@click.option(
    "-w",
    "--wallpaper",
    is_flag=True,
    help="Create one wallpaper pair using the tested HEVC all-intra pipeline; preserve inputs.",
)
@click.option(
    "--reference-video",
    type=click.Path(exists=True, path_type=pathlib.Path),
    default=None,
    help="Override the bundled experimental metadata reference with a compatible movie.",
)
@click.option(
    "--resolution",
    type=click.Choice(["1080x1920", "1080x1440"]),
    default="1080x1920",
    show_default=True,
    help="Wallpaper output size; center-cropped to fit.",
)
@click.option(
    "--quality",
    type=click.Choice(["high", "lower"]),
    default="high",
    show_default=True,
    help="Wallpaper video bitrate and HEIC quality preset.",
)
@click.option(
    "--duration",
    type=click.FloatRange(min=0, min_open=True),
    default=1612 / 600,
    help="Wallpaper clip length in seconds (limited by source duration).",
)
@click.option(
    "--still-image-time",
    type=click.FloatRange(min=0),
    default=None,
    help="Source key-frame timestamp; defaults to the tested template position.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=pathlib.Path),
    default=None,
    help="New output directory for one wallpaper pair; sources are preserved.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Print verbose output",
)
@click.option(
    "-p",
    "--pvt",
    is_flag=True,
    help="Save the Live Photo pair as a .pvt package. "
    "Unlike the default behavior, this will not modify the original files. "
    "The .pvt package can be imported into Photos as a Live Photo by double-clicking it.",
)
@click.option(
    "--manual",
    "-m",
    metavar="IMAGE VIDEO",
    nargs=2,
    multiple=True,
    type=click.Path(exists=True, path_type=pathlib.Path),
    help="Specify image and video files manually",
)
@click.argument(
    "files",
    nargs=-1,
    type=click.Path(exists=True, path_type=pathlib.Path),
)
def main(
    check: bool,
    wallpaper: bool,
    reference_video: pathlib.Path | None,
    duration: float,
    resolution: str,
    quality: str,
    still_image_time: float | None,
    output_dir: pathlib.Path | None,
    verbose: bool,
    pvt: bool,
    manual: tuple[tuple[pathlib.Path, pathlib.Path]],
    files: tuple[pathlib.Path, ...],
):
    """MakeLive: convert a photo (JPEG or HEIC) and video (MOV or MP4) pair to a Live Photo or Live Wallpaper.

    This will add the necessary metadata for Apple Photos to recognize the
    photo and video pair as a Live Photo when imported to Photos.

    Use --wallpaper to create an HEVC wallpaper candidate in a new directory.
    Sources are preserved; iPhone wallpaper motion requires device testing.

    MakeLive will attempt to find photo and video pairs in the FILES argument.
    Alternatively, you can specify the photo and video files manually using the
    --manual option: "--manual image_1234.jpg image_1234.mov"
    """

    if reference_video is not None and not wallpaper:
        raise click.UsageError("--reference-video requires --wallpaper.")
    if wallpaper and pvt:
        raise click.UsageError("--pvt cannot be combined with --wallpaper; import the generated pair into Photos together.")
    if not wallpaper:
        context = click.get_current_context()
        for name in ("resolution", "quality"):
            if context.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
                raise click.UsageError(f"--{name} requires --wallpaper.")

    if wallpaper and not check:
        jobs = []
        for image, video in manual:
            if not is_image_file(image) or not is_video_file(video):
                raise click.UsageError("--manual requires a JPEG/HEIC image and MOV/MP4 video.")
            jobs.append((image.resolve(), video.resolve()))
        for file in files:
            if not is_image_file(file) and not is_video_file(file):
                raise click.UsageError(f"Unsupported wallpaper input: {file}")
        video_stems = [file.stem for file in files if is_video_file(file)]
        if len(video_stems) != len(set(video_stems)):
            raise click.UsageError("Videos with the same stem must be converted separately.")
        pairs, unmatched = find_photo_video_pairs(files)
        jobs.extend(pairs)
        for file in unmatched:
            if not is_video_file(file):
                raise click.UsageError(f"No matching video for {file}; use --manual IMAGE VIDEO for different filenames.")
            jobs.append((None, file.resolve()))
        if not jobs:
            raise click.UsageError("No video files specified for wallpaper creation.")
        if output_dir is not None and len(jobs) != 1:
            raise click.UsageError("--output-dir supports one wallpaper pair at a time.")
        destinations = [output_dir.resolve() if output_dir else video.parent / f"{video.stem}_wallpaper" for image, video in jobs]
        if len(set(destinations)) != len(destinations):
            raise click.UsageError("Wallpaper inputs would use the same output directory.")
        for destination in destinations:
            if destination.exists():
                raise click.ClickException(f"Output directory already exists: {destination}")
        for (image, video), destination in zip(jobs, destinations):
            try:
                identifier, photo, movie = make_live_wallpaper(
                    video,
                    image_path=image,
                    duration=duration,
                    still_image_time=still_image_time,
                    output_dir=destination,
                    reference_video=reference_video,
                    resolution=resolution,
                    quality=quality,
                )
            except (ValueError, OSError, RuntimeError) as error:
                raise click.ClickException(str(error)) from error
            click.echo(f"Created wallpaper pair: {photo} and {movie}")
            if verbose:
                click.echo(f"Asset ID: {identifier}; resolution: {resolution}; quality: {quality}")
        return

    # if no files are passed (either via manual or files), print help and exit
    if not manual and not files:
        click.echo("No files specified", err=True)
        click.echo(main.get_help(click.Context(main)))
        raise click.Abort()

    # process manual files first
    for image, video in manual:
        if not is_image_file(image):
            click.echo(f"{image} is not a JPEG or HEIC image", err=True)
            raise click.Abort()
        if not is_video_file(video):
            click.echo(f"{video} is not a QuickTime movie file", err=True)
            raise click.Abort()
        if check:
            check_pair(image, video)
        else:
            if pvt:
                asset_id, pvt_file = save_live_photo_pair_as_pvt(image, video)
            else:
                asset_id = make_live_photo(image, video)
            if verbose:
                click.echo(f"Wrote asset ID: {asset_id} to {image} and {video}")
                if pvt:
                    click.echo(f"Saved {image} and {video} to {pvt_file}")

    # process any files passed via FILES argument
    matched_files, unmatched_files = find_photo_video_pairs(files)

    for image, video in matched_files:
        if check:
            check_pair(image, video)
        else:
            if pvt:
                asset_id, pvt_file = save_live_photo_pair_as_pvt(image, video)
            else:
                asset_id = make_live_photo(image, video)
            if verbose:
                click.echo(f"Wrote asset ID: {asset_id} to {image} and {video}")
                if pvt:
                    click.echo(f"Saved {image} and {video} to {pvt_file}")

    for file in unmatched_files:
        click.echo(f"No matching file pair found for {file}", err=True)


if __name__ == "__main__":
    main()
