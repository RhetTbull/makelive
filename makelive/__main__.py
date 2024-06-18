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
    save_live_photo_pair_as_pvt,
)
from .version import __version__


def find_photo_video_pairs(
    file_paths: Iterable[str | os.PathLike],
) -> tuple[list[tuple[pathlib.Path, pathlib.Path]], list[pathlib.Path]]:  # noqa: E501 (line too long
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
    verbose: bool,
    pvt: bool,
    manual: tuple[tuple[pathlib.Path, pathlib.Path]],
    files: tuple[pathlib.Path, ...],
):
    """MakeLive: convert a photo (JPEG or HEIC) and video (MOV or MP4) pair to a Live Photo.

    This will add the necessary metadata for Apple Photos to recognize the
    photo and video pair as a Live Photo when imported to Photos.

    Note: This will modify the image and video files in place and will result in
    loss of any XMP metadata stored in the video file. Ensure you have a backup
    if you need to preserve the original files.

    MakeLive will attempt to find photo and video pairs in the FILES argument.
    Alternatively, you can specify the photo and video files manually using the
    --manual option: "--manual image_1234.jpg image_1234.mov"

    Files that are not jpeg/heic or mov/mp4 will be ignored.
    """

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
