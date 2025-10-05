"""
Core package for MangaForge.

This package contains the core functionality for the manga downloader,
including the base provider, provider management, downloading, and
conversion utilities.
"""
from .base_provider import BaseProvider, ProviderError, MangaNotFoundError, ChapterNotFoundError
from .provider_manager import ProviderManager
from .downloader import Downloader
from .converter import Converter
from .config import Config
from .utils import *

__all__ = [
    'BaseProvider', 'ProviderError', 'MangaNotFoundError', 'ChapterNotFoundError',
    'ProviderManager', 'Downloader', 'Converter', 'Config'
]