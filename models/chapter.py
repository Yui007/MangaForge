"""
Chapter data models for MangaForge.

This module contains data structures for representing manga chapters
and their associated metadata.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Chapter:
    """
    Chapter information for a manga.

    This class represents a single chapter of a manga, including
    its metadata and access information.
    """
    chapter_id: str       # Provider-specific ID
    manga_id: str
    title: str
    chapter_number: str   # Can be "1", "1.5", "Extra", etc.
    volume: Optional[str]
    url: str
    release_date: Optional[str]
    language: str = "en"

    def __str__(self) -> str:
        """String representation for display purposes."""
        volume_str = f" Vol.{self.volume}" if self.volume else ""
        return f"Chapter {self.chapter_number}{volume_str}: {self.title}"

    @property
    def sort_key(self) -> float:
        """Get a numeric sort key for proper chapter ordering."""
        try:
            return float(self.chapter_number)
        except ValueError:
            # Handle special chapters like "Extra", "Special", etc.
            if self.chapter_number.lower() in ['extra', 'special', 'bonus']:
                return 999999.0  # Put at the end
            return 0.0

    @property
    def display_number(self) -> str:
        """Get formatted chapter number for display."""
        return self.chapter_number

    def is_special(self) -> bool:
        """Check if this is a special/extra chapter."""
        return self.chapter_number.lower() in ['extra', 'special', 'bonus']