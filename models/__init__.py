"""
Models package for MangaForge.

This package contains all data models used throughout the application.
"""
from .manga import MangaSearchResult, MangaInfo
from .chapter import Chapter

__all__ = ['MangaSearchResult', 'MangaInfo', 'Chapter']