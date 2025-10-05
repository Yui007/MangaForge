"""
Base provider abstract class for MangaForge.

This module defines the abstract base class that all manga providers
must implement. This class ensures consistency across all providers
and defines the contract that must be followed.

DO NOT MODIFY THIS FILE AFTER PHASE 1 - It is LOCKED.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import httpx
import logging

from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    """
    Abstract base class for all manga providers.

    All manga providers must inherit from this class and implement
    the required abstract methods. This ensures consistency and
    allows the provider manager to work with any provider.

    Provider implementations should:
    - Set provider_id, provider_name, and base_url as class attributes
    - Implement all abstract methods
    - Use the shared downloader and converter utilities
    - Handle errors gracefully with appropriate exceptions
    - Use logging instead of print statements
    """

    # Provider metadata (set in subclass)
    provider_id: str = ""        # e.g., "bato"
    provider_name: str = ""      # e.g., "Bato"
    base_url: str = ""           # e.g., "https://bato.to"

    def __init__(self):
        """Initialize the provider with HTTP client."""
        if not self.provider_id or not self.provider_name or not self.base_url:
            raise ValueError("Provider must set provider_id, provider_name, and base_url")

        self.session = httpx.Client(
            headers=self.get_headers(),
            timeout=30.0,
            follow_redirects=True
        )
        logger.info(f"Initialized provider: {self.provider_name} ({self.provider_id})")

    @abstractmethod
    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        """
        Search for manga by title.

        Args:
            query: Search query string
            page: Page number for pagination (1-indexed)

        Returns:
            Tuple of (results_list, has_next_page)
            - results_list: List of MangaSearchResult objects
            - has_next_page: Boolean indicating if more pages are available

        Raises:
            ProviderError: If search fails due to provider issues
            Exception: For unexpected errors
        """
        pass

    @abstractmethod
    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """
        Get detailed manga information.

        Args:
            manga_id: Provider-specific manga ID
            url: Direct URL to manga page (alternative to manga_id)

        Returns:
            MangaInfo object with complete manga details

        Raises:
            MangaNotFoundError: If manga doesn't exist
            ProviderError: If request fails
            ValueError: If neither manga_id nor url is provided
        """
        pass

    @abstractmethod
    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """
        Get all chapters for a manga.

        Args:
            manga_id: Provider-specific manga ID

        Returns:
            List of Chapter objects in correct reading order (oldest first)

        Raises:
            MangaNotFoundError: If manga doesn't exist
            ProviderError: If request fails
        """
        pass

    @abstractmethod
    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """
        Get all image URLs for a chapter.

        Args:
            chapter_id: Provider-specific chapter ID

        Returns:
            List of direct image URLs in reading order

        Raises:
            ChapterNotFoundError: If chapter doesn't exist
            ProviderError: If request fails
        """
        pass

    def download_image(self, url: str) -> bytes:
        """
        Download a single image.

        This is the default implementation that works for most providers.
        Override only if the provider needs special headers, cookies,
        or authentication for image downloads.

        Args:
            url: Direct URL to the image

        Returns:
            Image data as bytes

        Raises:
            ProviderError: If download fails
        """
        try:
            logger.debug(f"Downloading image: {url}")
            response = self.session.get(url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error downloading image {url}: {e}")
            raise ProviderError(f"Failed to download image: {e}")
        except Exception as e:
            logger.error(f"Unexpected error downloading image {url}: {e}")
            raise ProviderError(f"Failed to download image: {e}")

    def get_headers(self) -> Dict[str, str]:
        """
        Return HTTP headers for requests.

        Override this method if the provider needs special headers
        like authentication tokens, API keys, or custom user agents.

        Returns:
            Dictionary of HTTP headers
        """
        return {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': self.base_url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def __str__(self) -> str:
        """String representation of the provider."""
        return f"{self.provider_name} ({self.provider_id})"

    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"{self.__class__.__name__}(id='{self.provider_id}', name='{self.provider_name}', url='{self.base_url}')"


# Exception classes for provider errors
class ProviderError(Exception):
    """Base exception for provider-related errors."""
    pass


class MangaNotFoundError(ProviderError):
    """Exception raised when a manga is not found."""
    pass


class ChapterNotFoundError(ProviderError):
    """Exception raised when a chapter is not found."""
    pass