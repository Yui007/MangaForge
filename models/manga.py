"""
Manga data models for MangaForge.

This module contains the core data structures used throughout the application
for representing manga information.
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MangaSearchResult:
    """
    Result from search - minimal info for displaying search results.

    This class represents the basic information returned when searching
    for manga across different providers.
    """
    provider_id: str      # e.g., "bato"
    manga_id: str         # Provider-specific ID
    title: str
    cover_url: str
    url: str              # Direct URL to manga page

    def __str__(self) -> str:
        """String representation for display purposes."""
        return f"[{self.provider_id}] {self.title}"


@dataclass
class MangaInfo:
    """
    Detailed manga information retrieved from a provider.

    This class contains comprehensive information about a manga,
    including metadata, descriptions, and associated creators.
    """
    provider_id: str
    manga_id: str
    title: str
    alternative_titles: List[str]
    cover_url: str
    url: str
    description: str
    authors: List[str]
    artists: List[str]
    genres: List[str]
    status: str           # "Ongoing", "Completed", "Hiatus"
    year: Optional[int]

    def __str__(self) -> str:
        """String representation for display purposes."""
        return f"{self.title} ({self.year or 'Unknown'}) - {self.status}"

    @property
    def display_title(self) -> str:
        """Get the main title for display."""
        return self.title

    @property
    def all_titles(self) -> List[str]:
        """Get all titles including alternatives."""
        titles = [self.title]
        titles.extend(self.alternative_titles)
        return titles