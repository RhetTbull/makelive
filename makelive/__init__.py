"""Add the necessary metadata to photo + video pair so Photos recognizes them as Live Photos when imported"""

from .makelive import (
    is_image_file,
    is_live_photo_pair,
    is_video_file,
    live_id,
    make_live_photo,
    make_live_wallpaper,
    save_live_photo_pair_as_pvt,
)
from .version import __version__

__all__ = [
    "__version__",
    "is_image_file",
    "is_live_photo_pair",
    "is_video_file",
    "live_id",
    "make_live_photo",
    "make_live_wallpaper",
    "save_live_photo_pair_as_pvt",
]
