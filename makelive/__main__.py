"""Command line interface for makelive."""


from __future__ import annotations

import pathlib

import click

from .makelive import make_live_photo
from .version import __version__


@click.command()
@click.version_option(version=__version__)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Print verbose output",
)
@click.argument("image", type=click.Path(exists=True, path_type=pathlib.Path))
@click.argument("video", type=click.Path(exists=True, path_type=pathlib.Path))
def main(verbose: bool, image: pathlib.Path, video: pathlib.Path):
    """Convert a photo (JPEG or HEIC) and video (MOV or MP4) to a Live Photo.

    This will add the necessary metadata for Apple Photos to recognize the
    photo and video pair as a Live Photo when imported to Photos.

    Note: This will modify the image and video files in place and will result in
    loss of any XMP metadata stored in the video file. Ensure you have a backup
    if you need to preserve the original files.
    """
    if image.suffix.lower() not in [".jpg", ".jpeg", ".heic", ".heif"]:
        click.echo(f"{image} is not a JPEG or HEIC image", err=True)
        raise click.Abort()
    if video.suffix.lower() not in [".mov", ".mp4"]:
        click.echo(f"{video} is not a QuickTime movie file", err=True)
        raise click.Abort()
    asset_id = make_live_photo(image, video)
    if verbose:
        click.echo(f"Wrote asset ID: {asset_id} to {image} and {video}")


if __name__ == "__main__":
    main()