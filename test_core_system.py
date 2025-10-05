#!/usr/bin/env python3
"""
Test script for MangaForge Phase 1 core system.

This script tests all the core components to ensure they work correctly
with the mock provider before moving to Phase 2.
"""
import logging
import sys
from pathlib import Path

# Add the current directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_provider_auto_discovery():
    """Test that ProviderManager can auto-discover the mock provider."""
    logger.info("Testing provider auto-discovery...")

    try:
        from core.provider_manager import ProviderManager

        manager = ProviderManager()

        # Check if mock provider was loaded
        if 'mock' not in manager.providers:
            logger.error("Mock provider not auto-discovered!")
            return False

        logger.info(f"✓ Auto-discovered providers: {list(manager.providers.keys())}")

        # Test getting provider by ID
        mock_provider = manager.get_provider('mock')
        logger.info(f"✓ Got mock provider: {mock_provider}")

        # Test provider info
        info = manager.get_provider_info('mock')
        logger.info(f"✓ Provider info: {info}")

        return True

    except Exception as e:
        logger.error(f"Provider auto-discovery test failed: {e}")
        return False


def test_mock_provider_functionality():
    """Test that the mock provider works correctly."""
    logger.info("Testing mock provider functionality...")

    try:
        from core.provider_manager import ProviderManager

        manager = ProviderManager()
        mock_provider = manager.get_provider('mock')

        # Test search
        logger.info("Testing search...")
        results, has_next = mock_provider.search("test manga", page=1)

        if not results:
            logger.error("Search returned no results!")
            return False

        logger.info(f"✓ Search returned {len(results)} results")
        logger.info(f"  First result: {results[0]}")

        # Test get_manga_info
        logger.info("Testing get_manga_info...")
        manga_info = mock_provider.get_manga_info(manga_id="test_id")

        logger.info(f"✓ Got manga info: {manga_info.title}")
        logger.info(f"  Genres: {manga_info.genres}")
        logger.info(f"  Status: {manga_info.status}")

        # Test get_chapters
        logger.info("Testing get_chapters...")
        chapters = mock_provider.get_chapters("test_id")

        if not chapters:
            logger.error("get_chapters returned no chapters!")
            return False

        logger.info(f"✓ Got {len(chapters)} chapters")
        logger.info(f"  First chapter: {chapters[0]}")
        logger.info(f"  Last chapter: {chapters[-1]}")

        # Test get_chapter_images
        logger.info("Testing get_chapter_images...")
        image_urls = mock_provider.get_chapter_images(chapters[0].chapter_id)

        if not image_urls:
            logger.error("get_chapter_images returned no image URLs!")
            return False

        logger.info(f"✓ Got {len(image_urls)} image URLs")
        logger.info(f"  First image URL: {image_urls[0]}")

        # Test download_image
        logger.info("Testing download_image...")
        image_data = mock_provider.download_image(image_urls[0])

        if not image_data:
            logger.error("download_image returned no data!")
            return False

        logger.info(f"✓ Downloaded image data: {len(image_data)} bytes")

        return True

    except Exception as e:
        logger.error(f"Mock provider functionality test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_system():
    """Test that the configuration system works correctly."""
    logger.info("Testing configuration system...")

    try:
        from core.config import Config

        config = Config()

        # Test basic config access
        download_dir = config.download_dir
        max_workers = config.max_chapter_workers
        default_format = config.default_format

        logger.info(f"✓ Download directory: {download_dir}")
        logger.info(f"✓ Max chapter workers: {max_workers}")
        logger.info(f"✓ Default format: {default_format}")

        # Test provider rate limits
        mock_rate_limit = config.get_rate_limit('mock')
        logger.info(f"✓ Mock provider rate limit: {mock_rate_limit}s")

        return True

    except Exception as e:
        logger.error(f"Configuration system test failed: {e}")
        return False


def test_downloader():
    """Test the downloader with mock data."""
    logger.info("Testing downloader...")

    try:
        from core.provider_manager import ProviderManager
        from core.downloader import Downloader
        from core.config import Config
        from pathlib import Path
        import tempfile

        # Create temporary directory for downloads
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            manager = ProviderManager()
            mock_provider = manager.get_provider('mock')
            downloader = Downloader()
            config = Config()

            # Get test data
            manga_info = mock_provider.get_manga_info(manga_id="test_manga")
            chapters = mock_provider.get_chapters("test_manga")[:2]  # Just first 2 chapters

            logger.info(f"Testing download of {len(chapters)} chapters...")

            # Test downloading chapters
            downloaded_paths = downloader.download_chapters(
                mock_provider,
                manga_info,
                chapters,
                temp_path
            )

            logger.info(f"✓ Downloaded {len(downloaded_paths)} chapters")

            # Check that files were created
            for path in downloaded_paths:
                if path.exists():
                    logger.info(f"✓ Chapter directory exists: {path}")
                    # Check for image files
                    image_files = list(path.glob("*.jpg"))
                    logger.info(f"  Contains {len(image_files)} images")
                else:
                    logger.error(f"✗ Chapter directory not found: {path}")
                    return False

            return True

    except Exception as e:
        logger.error(f"Downloader test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_converter():
    """Test the converter functionality."""
    logger.info("Testing converter...")

    try:
        from core.converter import Converter
        from pathlib import Path
        import tempfile

        # Create temporary directory with fake images
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_images_dir = temp_path / "fake_images"
            fake_images_dir.mkdir()

            # Create some fake image files
            for i in range(5):
                fake_image = fake_images_dir / f"page_{i:03d}.jpg"
                fake_image.write_bytes(b"fake image data " * 100)  # 1500 bytes

            converter = Converter()

            # Test CBZ conversion
            cbz_path = temp_path / "test.cbz"
            result_path = converter.to_cbz(fake_images_dir, cbz_path, delete_images=False)

            if result_path.exists():
                logger.info(f"✓ CBZ created successfully: {result_path}")
                logger.info(f"  File size: {result_path.stat().st_size} bytes")
            else:
                logger.error("✗ CBZ file not created!")
                return False

            # Test PDF conversion (if dependencies available)
            try:
                pdf_path = temp_path / "test.pdf"
                result_path = converter.to_pdf(fake_images_dir, pdf_path, delete_images=False)

                if result_path.exists():
                    logger.info(f"✓ PDF created successfully: {result_path}")
                    logger.info(f"  File size: {result_path.stat().st_size} bytes")
                else:
                    logger.warning("⚠ PDF file not created (missing dependencies?)")

            except Exception as e:
                logger.warning(f"⚠ PDF conversion failed (expected if dependencies missing): {e}")

            return True

    except Exception as e:
        logger.error(f"Converter test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    logger.info("Starting MangaForge Phase 1 core system tests...")

    tests = [
        ("Provider Auto-Discovery", test_provider_auto_discovery),
        ("Mock Provider Functionality", test_mock_provider_functionality),
        ("Configuration System", test_config_system),
        ("Downloader", test_downloader),
        ("Converter", test_converter),
    ]

    results = []

    for test_name, test_func in tests:
        logger.info(f"\n{'='*50}")
        logger.info(f"Running test: {test_name}")
        logger.info(f"{'='*50}")

        try:
            success = test_func()
            results.append((test_name, success))

            if success:
                logger.info(f"✓ {test_name} PASSED")
            else:
                logger.error(f"✗ {test_name} FAILED")

        except Exception as e:
            logger.error(f"✗ {test_name} FAILED with exception: {e}")
            results.append((test_name, False))

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("TEST SUMMARY")
    logger.info(f"{'='*60}")

    passed = 0
    failed = 0

    for test_name, success in results:
        status = "PASSED" if success else "FAILED"
        logger.info(f"{test_name:30} : {status}")

        if success:
            passed += 1
        else:
            failed += 1

    logger.info(f"{'='*60}")
    logger.info(f"Total: {len(results)} tests")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")

    if failed == 0:
        logger.info("🎉 All tests passed! Phase 1 core system is ready!")
        return 0
    else:
        logger.error("❌ Some tests failed. Please fix the issues before proceeding.")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)