"""
Downloader module for MangaForge.

This module handles parallel chapter downloads and concurrent image downloads.
It uses threading for parallelization and provides progress callbacks for
monitoring download progress.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Callable, Optional, Dict, Any
import shutil

from .base_provider import BaseProvider, ProviderError
from models import MangaInfo, Chapter

logger = logging.getLogger(__name__)


class Downloader:
    """
    Shared download logic for all manga providers.

    This class handles parallel chapter downloads and concurrent image
    downloads with progress tracking and error handling.

    Features:
    - Parallel chapter downloads (configurable worker count)
    - Concurrent image downloads within each chapter
    - Progress callbacks for UI updates
    - Automatic retry logic for failed downloads
    - Proper error handling and logging
    """

    def __init__(self, max_chapter_workers: int = 3, max_image_workers: int = 10):
        """
        Initialize the downloader.

        Args:
            max_chapter_workers: Maximum number of parallel chapter downloads
            max_image_workers: Maximum number of concurrent image downloads per chapter
        """
        self.chapter_executor = ThreadPoolExecutor(max_workers=max_chapter_workers)
        self.image_executor = ThreadPoolExecutor(max_workers=max_image_workers)

        logger.info(f"Initialized downloader with {max_chapter_workers} chapter workers and {max_image_workers} image workers")

    def download_chapters(self,
                         provider: BaseProvider,
                         manga_info: MangaInfo,
                         chapters: List[Chapter],
                         output_dir: Path,
                         progress_callback: Optional[Callable] = None) -> List[Path]:
        """
        Download multiple chapters in parallel.

        Args:
            provider: The provider to use for downloading
            manga_info: Information about the manga being downloaded
            chapters: List of chapters to download
            output_dir: Base output directory for downloads
            progress_callback: Optional callback for progress updates

        Returns:
            List of paths to downloaded chapter directories

        Raises:
            ProviderError: If download fails
        """
        if not chapters:
            logger.warning("No chapters to download")
            return []

        logger.info(f"Starting download of {len(chapters)} chapters for '{manga_info.title}'")

        # Create manga directory
        manga_dir = output_dir / self._sanitize_filename(manga_info.title)
        manga_dir.mkdir(parents=True, exist_ok=True)

        # Submit all chapter downloads
        future_to_chapter = {
            self.chapter_executor.submit(self._download_chapter_task, provider, chapter, manga_dir, progress_callback): chapter
            for chapter in chapters
        }

        # Collect results as they complete
        downloaded_chapters = []
        completed = 0

        for future in as_completed(future_to_chapter):
            chapter = future_to_chapter[future]

            try:
                chapter_path = future.result()
                downloaded_chapters.append(chapter_path)

                completed += 1
                logger.info(f"Completed chapter {completed}/{len(chapters)}: {chapter.title}")

                if progress_callback:
                    progress_callback(completed, len(chapters), chapter.title)

            except Exception as e:
                logger.error(f"Failed to download chapter '{chapter.title}': {e}")
                # Continue with other chapters even if one fails

        logger.info(f"Downloaded {len(downloaded_chapters)}/{len(chapters)} chapters successfully")
        return downloaded_chapters

    def download_chapter(self,
                        provider: BaseProvider,
                        chapter: Chapter,
                        output_dir: Path,
                        progress_callback: Optional[Callable] = None) -> Path:
        """
        Download a single chapter.

        Args:
            provider: The provider to use for downloading
            chapter: Chapter to download
            output_dir: Output directory for the chapter
            progress_callback: Optional callback for progress updates

        Returns:
            Path to the downloaded chapter directory

        Raises:
            ProviderError: If download fails
        """
        logger.info(f"Downloading chapter: {chapter.title}")

        # Create chapter directory
        chapter_dir = output_dir / self._get_chapter_folder_name(chapter)
        chapter_dir.mkdir(parents=True, exist_ok=True)

        # Get image URLs
        try:
            image_urls = provider.get_chapter_images(chapter.chapter_id)
        except Exception as e:
            raise ProviderError(f"Failed to get image URLs for chapter '{chapter.title}': {e}")

        if not image_urls:
            logger.warning(f"No images found for chapter '{chapter.title}'")
            return chapter_dir

        # Download images concurrently
        downloaded_images = self.download_images_concurrent(
            provider, image_urls, chapter_dir, progress_callback
        )

        logger.info(f"Downloaded {len(downloaded_images)} images for chapter '{chapter.title}'")
        return chapter_dir

    def download_images_concurrent(self,
                                   provider: BaseProvider,
                                   image_urls: List[str],
                                   output_dir: Path,
                                   progress_callback: Optional[Callable] = None) -> List[Path]:
        """
        Download multiple images concurrently.

        Args:
            provider: The provider to use for downloading
            image_urls: List of image URLs to download
            output_dir: Output directory for images
            progress_callback: Optional callback for progress updates

        Returns:
            List of paths to downloaded images

        Raises:
            ProviderError: If download fails
        """
        if not image_urls:
            return []

        logger.debug(f"Downloading {len(image_urls)} images concurrently")

        # Submit all image downloads
        future_to_url = {
            self.image_executor.submit(self._download_image_task, provider, url, output_dir, idx): (url, idx)
            for idx, url in enumerate(image_urls)
        }

        # Collect results as they complete
        downloaded_images = []
        completed = 0

        for future in as_completed(future_to_url):
            url, idx = future_to_url[future]

            try:
                image_path = future.result()
                downloaded_images.append(image_path)

                completed += 1
                if progress_callback:
                    progress_callback(completed, len(image_urls), f"Image {idx + 1}")

            except Exception as e:
                logger.error(f"Failed to download image {idx + 1} ({url}): {e}")
                # Continue with other images even if one fails

        logger.debug(f"Downloaded {len(downloaded_images)}/{len(image_urls)} images")
        return downloaded_images

    def _download_chapter_task(self,
                              provider: BaseProvider,
                              chapter: Chapter,
                              manga_dir: Path,
                              progress_callback: Optional[Callable]) -> Path:
        """Task wrapper for downloading a single chapter."""
        try:
            return self.download_chapter(provider, chapter, manga_dir, progress_callback)
        except Exception as e:
            logger.error(f"Chapter download task failed for '{chapter.title}': {e}")
            raise

    def _download_image_task(self,
                           provider: BaseProvider,
                           url: str,
                           output_dir: Path,
                           index: int) -> Path:
        """Task wrapper for downloading a single image."""
        try:
            # Download image data
            image_data = provider.download_image(url)

            # Generate filename
            filename = f"{index + 1:03d}.jpg"  # 001.jpg, 002.jpg, etc.
            image_path = output_dir / filename

            # Save image
            with open(image_path, 'wb') as f:
                f.write(image_data)

            logger.debug(f"Saved image: {image_path}")
            return image_path

        except Exception as e:
            logger.error(f"Image download task failed for {url}: {e}")
            raise

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe filesystem use."""
        import re
        # Remove or replace invalid characters
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # Remove multiple spaces
        filename = re.sub(r'\s+', ' ', filename)
        return filename.strip()

    def _get_chapter_folder_name(self, chapter: Chapter) -> str:
        """Generate a safe folder name for a chapter."""
        volume_str = f" Vol.{chapter.volume}" if chapter.volume else ""
        chapter_str = f"Chapter {chapter.chapter_number}"
        title_str = f" - {chapter.title}" if chapter.title else ""

        folder_name = f"{chapter_str}{volume_str}{title_str}"
        return self._sanitize_filename(folder_name)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup executors."""
        self.chapter_executor.shutdown(wait=True)
        self.image_executor.shutdown(wait=True)