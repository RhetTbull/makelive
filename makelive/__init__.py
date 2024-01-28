"""Add the necessary metadata to photo + video pair so Photos recognizes them as Live Photos when imported"""

from .makelive import make_live_photo
from .version import __version__

__all__ = ["__version__", "make_live_photo"]
