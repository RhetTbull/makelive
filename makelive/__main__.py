"""Command line interface for makelive."""


from __future__ import annotations

import pathlib

import click

from .makelive import make_live_photo
from .version import __version__

IMAGE_EXTENSIONS = [".jpeg", ".jpg", ".heic", ".heif"]
VIDEO_EXTENSIONS = [".mov", ".mp4"]


def find_photo_video_pairs(
    file_paths: list[str],
) -> tuple[list[tuple[str, str]], list[str]]:  # noqa: E501 (line too long
    """Find photo and video pairs in a list of file paths."""
    matched_files, unmatched_files, image_files, video_files = [], [], {}, {}

    for file_path in file_paths:
        file_path = pathlib.Path(file_path)
        file_stem = file_path.stem
        if file_path.suffix.lower() in IMAGE_EXTENSIONS:
            image_files[file_stem] = str(file_path.resolve())
        elif file_path.suffix.lower() in VIDEO_EXTENSIONS:
            video_files[file_stem] = str(file_path.resolve())

    for key, image_file in image_files.items():
        if key in video_files:
            matched_files.append((image_file, video_files[key]))
            del video_files[key]
        else:
            unmatched_files.append(image_file)

    unmatched_files.extend(video_files.values())

    return matched_files, unmatched_files


@click.command()
@click.version_option(version=__version__)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Print verbose output",
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
    verbose: bool,
    manual: tuple[tuple[pathlib.Path, pathlib.Path]],
    files: tuple[pathlib.Path],
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
        if image.suffix.lower() not in IMAGE_EXTENSIONS:
            click.echo(f"{image} is not a JPEG or HEIC image", err=True)
            raise click.Abort()
        if video.suffix.lower() not in VIDEO_EXTENSIONS:
            click.echo(f"{video} is not a QuickTime movie file", err=True)
            raise click.Abort()
        asset_id = make_live_photo(image, video)
        if verbose:
            click.echo(f"Wrote asset ID: {asset_id} to {image} and {video}")

    # process any files passed via FILES argument
    matched_files, unmatched_files = find_photo_video_pairs(files)

    for image, video in matched_files:
        asset_id = make_live_photo(image, video)
        if verbose:
            click.echo(f"Wrote asset ID: {asset_id} to {image} and {video}")
    for file in unmatched_files:
        click.echo(f"No matching file pair found for {file}", err=True)


if __name__ == "__main__":
    main()
