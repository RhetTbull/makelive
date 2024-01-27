"""Add the necessary metadata to photo + video pair so Photos recognizes them as Live Photos when imported"""

from .version import __version__
from .makelive import make_live_photo

__all__ = ["__version__", "make_live_photo"]
