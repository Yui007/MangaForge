"""
Mock provider for MangaForge testing.

This provider returns fake data for testing purposes. It implements
all the required BaseProvider methods but doesn't actually scrape
any real websites.

Use this provider to test the entire MangaForge system without
making real HTTP requests.
"""
import logging
from typing import List, Optional
from random import randint, choice

from core.base_provider import BaseProvider
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)


class MockProvider(BaseProvider):
    """
    Mock provider that returns fake data for testing.

    This provider generates realistic but fake manga data for testing
    the entire MangaForge system. It's useful for:

    - Testing the core system without real providers
    - Testing the CLI interface
    - Testing download and conversion functionality
    - Development and debugging

    Features:
    - Generates fake manga search results
    - Creates realistic manga information
    - Provides fake chapter lists
    - Returns mock image URLs
    - No real HTTP requests made
    """

    provider_id = "mock"
    provider_name = "Mock Provider"
    base_url = "https://mock.example.com"

    def __init__(self):
        """Initialize the mock provider."""
        super().__init__()
        logger.info("Mock provider initialized - generating fake data")

    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        """
        Search for manga (returns fake results).

        Args:
            query: Search query string
            page: Page number for pagination

        Returns:
            Tuple of (fake_results, has_next_page)
        """
        logger.debug(f"Mock search for '{query}' on page {page}")

        # Generate fake search results
        results = []
        num_results = randint(5, 15)  # Random number of results

        for i in range(num_results):
            result = MangaSearchResult(
                provider_id=self.provider_id,
                manga_id=f"mock_manga_{i + 1}",
                title=f"{query} Volume {randint(1, 20)} - Part {i + 1}",
                cover_url=f"https://mock.example.com/covers/{randint(1000, 9999)}.jpg",
                url=f"https://mock.example.com/manga/mock_manga_{i + 1}"
            )
            results.append(result)

        # Simulate pagination
        has_next = page < 3  # Only 3 pages of results

        logger.info(f"Mock search returned {len(results)} results")
        return results, has_next

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """
        Get detailed manga information (fake data).

        Args:
            manga_id: Provider-specific manga ID
            url: Direct URL to manga page

        Returns:
            MangaInfo object with fake but realistic data
        """
        if manga_id:
            logger.debug(f"Mock get_manga_info for ID: {manga_id}")
        else:
            logger.debug(f"Mock get_manga_info for URL: {url}")

        # Generate fake manga info
        genres = ["Action", "Adventure", "Comedy", "Drama", "Fantasy", "Romance"]
        authors = ["Mock Author", "Test Writer", "Fake Creator"]
        artists = ["Mock Artist", "Test Illustrator", "Fake Drawer"]

        manga_info = MangaInfo(
            provider_id=self.provider_id,
            manga_id=manga_id or "mock_manga_1",
            title="Mock Manga Title - The Ultimate Test",
            alternative_titles=[
                "Mock Manga Alternative Title",
                "モックマンガ",
                "테스트 만화"
            ],
            cover_url="https://mock.example.com/covers/1234.jpg",
            url=url or "https://mock.example.com/manga/mock_manga_1",
            description="This is a mock description for testing purposes. " * 3,
            authors=[choice(authors) for _ in range(randint(1, 2))],
            artists=[choice(artists) for _ in range(randint(1, 2))],
            genres=[choice(genres) for _ in range(randint(3, 6))],
            status=choice(["Ongoing", "Completed", "Hiatus"]),
            year=randint(2010, 2024)
        )

        logger.info(f"Mock get_manga_info returned: {manga_info.title}")
        return manga_info

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """
        Get all chapters for a manga (fake data).

        Args:
            manga_id: Provider-specific manga ID

        Returns:
            List of fake Chapter objects
        """
        logger.debug(f"Mock get_chapters for: {manga_id}")

        chapters = []
        num_chapters = randint(50, 200)  # Random number of chapters

        for i in range(1, num_chapters + 1):
            # Generate realistic chapter numbers
            if randint(1, 10) == 1:  # 10% chance of special chapter
                chapter_num = choice(["Extra", "Special", "Bonus"])
                title = f"Special Chapter {i}"
            else:
                chapter_num = str(i)
                if randint(1, 5) == 1:  # 20% chance of decimal chapter
                    chapter_num = f"{i}.5"
                title = f"Chapter {chapter_num}: Mock Chapter Title {i}"

            chapter = Chapter(
                chapter_id=f"mock_chapter_{i}",
                manga_id=manga_id,
                title=title,
                chapter_number=chapter_num,
                volume=str(randint(1, 20)) if randint(1, 3) != 1 else None,  # 66% have volumes
                url=f"https://mock.example.com/chapter/mock_chapter_{i}",
                release_date=f"2024-{randint(1, 12):02d}-{randint(1, 28):02d}",
                language="en"
            )
            chapters.append(chapter)

        # Sort chapters by number (special chapters at end)
        def sort_key(chapter):
            try:
                num = float(chapter.chapter_number)
                return (0, num)  # Regular chapters first
            except ValueError:
                return (1, 0)    # Special chapters last

        chapters.sort(key=sort_key)

        logger.info(f"Mock get_chapters returned {len(chapters)} chapters")
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """
        Get all image URLs for a chapter (fake data).

        Args:
            chapter_id: Provider-specific chapter ID

        Returns:
            List of fake image URLs
        """
        logger.debug(f"Mock get_chapter_images for: {chapter_id}")

        # Generate random number of images per chapter
        num_images = randint(30, 80)

        image_urls = []
        for i in range(1, num_images + 1):
            # Generate fake image URLs
            image_url = f"https://mock.example.com/images/{chapter_id}/page_{i:03d}.jpg"
            image_urls.append(image_url)

        logger.info(f"Mock get_chapter_images returned {len(image_urls)} image URLs")
        return image_urls

    def download_image(self, url: str) -> bytes:
        """
        Download a single image (returns fake image data).

        Args:
            url: Image URL to download

        Returns:
            Fake image data as bytes
        """
        logger.debug(f"Mock download_image for: {url}")

        # Generate fake image data (random bytes)
        fake_image_size = randint(50 * 1024, 500 * 1024)  # 50KB to 500KB
        fake_image_data = bytes(randint(0, 255) for _ in range(fake_image_size))

        logger.debug(f"Mock download_image returned {len(fake_image_data)} bytes")
        return fake_image_data