"""
CLI package for MangaForge.

This package contains the beautiful interactive CLI interface
built with Rich and Typer for the MangaForge manga downloader.
"""
from .app import MangaForgeApp
from .tables import *
from .menus import *
from .prompts import *

__all__ = ['MangaForgeApp']