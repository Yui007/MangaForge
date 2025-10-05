"""
Utility functions for MangaForge.

This module contains shared helper functions used throughout the application,
including HTTP utilities, file operations, and common helper functions.
"""
import logging
import re
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
import httpx

from models import Chapter, MangaInfo

logger = logging.getLogger(__name__)


def download_image_default(url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
    """
    Default image download function.

    This is the standard implementation for downloading images that works
    for most providers. Providers can override this if they need special
    handling for images.

    Args:
        url: Direct URL to the image
        headers: Optional HTTP headers to use

    Returns:
        Image data as bytes

    Raises:
        ProviderError: If download fails
    """
    try:
        logger.debug(f"Downloading image: {url}")

        # Use default headers if none provided
        if headers is None:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://example.com',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Accept-Language': 'en-US,en;q=0.9',
                'Connection': 'keep-alive',
            }

        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error downloading image {url}: {e}")
        raise ProviderError(f"Failed to download image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error downloading image {url}: {e}")
        raise ProviderError(f"Failed to download image: {e}")


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename for safe filesystem use.

    Removes or replaces characters that are invalid for filenames
    across different operating systems.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename safe for filesystem use
    """
    # Replace invalid characters with underscores
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

    # Replace multiple spaces with single space
    filename = re.sub(r'\s+', ' ', filename)

    # Remove leading/trailing whitespace and dots
    filename = filename.strip(' .')

    # Ensure filename is not empty
    if not filename:
        filename = "untitled"

    # Truncate very long filenames
    if len(filename) > 255:
        name, ext = Path(filename).stem, Path(filename).suffix
        max_name_length = 255 - len(ext)
        filename = name[:max_name_length] + ext

    return filename


def get_chapter_path(manga_info: MangaInfo, chapter: Chapter) -> Path:
    """
    Generate the full path for a chapter directory.

    Args:
        manga_info: Information about the manga
        chapter: Chapter information

    Returns:
        Full path where chapter should be saved
    """
    from .config import Config

    config = Config()

    # Create manga directory
    manga_dir = config.download_dir / sanitize_filename(manga_info.title)

    # Create chapter subdirectory
    volume_str = f" Vol.{chapter.volume}" if chapter.volume else ""
    chapter_str = f"Chapter {chapter.chapter_number}"
    title_str = f" - {chapter.title}" if chapter.title else ""

    chapter_folder = f"{chapter_str}{volume_str}{title_str}"
    chapter_dir = manga_dir / sanitize_filename(chapter_folder)

    return chapter_dir


def parse_chapter_range(chapter_range: str, available_chapters: List[Chapter]) -> List[Chapter]:
    """
    Parse chapter range string and return matching chapters.

    Supports formats like:
    - "1-10" (chapters 1 through 10)
    - "1,3,5" (specific chapters)
    - "1-5,10,15-20" (mix of ranges and singles)

    Args:
        chapter_range: Range string to parse
        available_chapters: List of all available chapters

    Returns:
        List of chapters matching the range

    Raises:
        ValueError: If range format is invalid
    """
    if not chapter_range or not chapter_range.strip():
        return []

    selected_chapters = []
    chapters_by_number = {float(chapter.chapter_number): chapter for chapter in available_chapters}

    # Split by comma to handle multiple ranges
    parts = [part.strip() for part in chapter_range.split(',')]

    for part in parts:
        if '-' in part:
            # Handle range (e.g., "1-10")
            try:
                start_str, end_str = part.split('-', 1)
                start = float(start_str.strip())
                end = float(end_str.strip())

                # Find all chapters in range
                for chapter_num in chapters_by_number:
                    if start <= chapter_num <= end:
                        selected_chapters.append(chapters_by_number[chapter_num])

            except ValueError:
                raise ValueError(f"Invalid range format: {part}")

        else:
            # Handle single chapter
            try:
                chapter_num = float(part.strip())
                if chapter_num in chapters_by_number:
                    selected_chapters.append(chapters_by_number[chapter_num])
                else:
                    logger.warning(f"Chapter {chapter_num} not found")
            except ValueError:
                raise ValueError(f"Invalid chapter number: {part}")

    # Sort by chapter number
    selected_chapters.sort(key=lambda c: float(c.chapter_number))

    return selected_chapters


def rate_limit(provider_id: str, config: Optional[Any] = None):
    """
    Apply rate limiting for a provider.

    Args:
        provider_id: ID of the provider to rate limit
        config: Configuration object (uses default if None)
    """
    if config is None:
        from .config import Config
        config = Config()

    delay = config.get_rate_limit(provider_id)

    if delay > 0:
        logger.debug(f"Rate limiting {provider_id}: sleeping for {delay}s")
        time.sleep(delay)


def ensure_directory(path: Path):
    """
    Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path to create
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {path}")
    except Exception as e:
        logger.error(f"Failed to create directory {path}: {e}")
        raise


def get_file_size_mb(path: Path) -> float:
    """
    Get file size in megabytes.

    Args:
        path: Path to file

    Returns:
        File size in MB
    """
    if not path.exists():
        return 0.0

    size_bytes = path.stat().st_size
    return size_bytes / (1024 * 1024)


def format_bytes(bytes_count: float) -> str:
    """
    Format bytes count into human readable string.

    Args:
        bytes_count: Number of bytes

    Returns:
        Formatted string (e.g., "1.5 MB", "2.3 GB")
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_count < 1024.0:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.1f} TB"


def validate_url(url: str) -> bool:
    """
    Validate if a string is a valid URL.

    Args:
        url: URL string to validate

    Returns:
        True if valid URL, False otherwise
    """
    try:
        result = httpx.URL(url)
        return bool(result.scheme and result.host)
    except Exception:
        return False


def extract_domain(url: str) -> str:
    """
    Extract domain from URL.

    Args:
        url: URL to extract domain from

    Returns:
        Domain name (e.g., "example.com")
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc
    except Exception:
        return ""


def retry_request(func, max_attempts: int = 3, delay: float = 1.0):
    """
    Retry a function call with exponential backoff.

    Args:
        func: Function to retry (should be callable)
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds

    Returns:
        Result of the function call

    Raises:
        Last exception if all retries fail
    """
    last_exception = None

    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as e:
            last_exception = e
            if attempt < max_attempts - 1:
                sleep_time = delay * (2 ** attempt)  # Exponential backoff
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
            else:
                logger.error(f"All {max_attempts} attempts failed. Last error: {e}")

    if last_exception:
        raise last_exception
    else:
        raise RuntimeError("Function failed after all retry attempts")


class ProviderError(Exception):
    """Exception raised by provider-related errors."""
    pass