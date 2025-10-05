#!/usr/bin/env python3
"""
Test script for MangaForge Phase 2 CLI system.

This script tests the CLI components to ensure they can be imported
and initialized correctly. Full interactive testing requires manual
user interaction.
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


def test_cli_imports():
    """Test that all CLI components can be imported."""
    logger.info("Testing CLI imports...")

    try:
        # Test main entry point
        from main import main, check_dependencies
        logger.info("✓ main.py imports successful")

        # Test CLI app
        from cli.app import MangaForgeApp
        logger.info("✓ cli.app imports successful")

        # Test CLI components
        from cli.tables import display_search_results, display_manga_info_card
        logger.info("✓ cli.tables imports successful")

        from cli.menus import select_chapters, select_download_format
        logger.info("✓ cli.menus imports successful")

        from cli.prompts import prompt_manga_title, prompt_manga_url
        logger.info("✓ cli.prompts imports successful")

        return True

    except Exception as e:
        logger.error(f"CLI imports test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cli_initialization():
    """Test that CLI components can be initialized."""
    logger.info("Testing CLI initialization...")

    try:
        # Test MangaForgeApp initialization
        from cli.app import MangaForgeApp

        app = MangaForgeApp()
        logger.info("✓ MangaForgeApp initialized successfully")

        # Check that components are properly initialized
        assert app.config is not None
        assert app.provider_manager is not None
        assert app.downloader is not None
        assert app.converter is not None

        logger.info("✓ All CLI components initialized correctly")

        return True

    except Exception as e:
        logger.error(f"CLI initialization test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cli_dependencies():
    """Test that CLI dependencies are available."""
    logger.info("Testing CLI dependencies...")

    try:
        # Test Rich components
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.progress import Progress

        console = Console()
        table = Table()
        panel = Panel("test")
        progress = Progress()

        logger.info("✓ Rich components available")

        # Test Typer
        import typer
        logger.info("✓ Typer available")

        # Test Questionary
        import questionary
        logger.info("✓ Questionary available")

        return True

    except Exception as e:
        logger.error(f"CLI dependencies test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_provider_integration():
    """Test that CLI integrates properly with providers."""
    logger.info("Testing CLI provider integration...")

    try:
        from cli.app import MangaForgeApp

        app = MangaForgeApp()

        # Test provider manager integration
        providers = app.provider_manager.list_providers()
        logger.info(f"✓ Provider manager loaded {len(providers)} providers: {providers}")

        # Test that mock provider is available
        if 'mock' in providers:
            mock_provider = app.provider_manager.get_provider('mock')
            logger.info(f"✓ Mock provider available: {mock_provider}")

            # Test basic provider functionality
            results, has_next = mock_provider.search("test", page=1)
            logger.info(f"✓ Mock provider search works: {len(results)} results")

            return True
        else:
            logger.error("Mock provider not found!")
            return False

    except Exception as e:
        logger.error(f"Provider integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_config_integration():
    """Test that CLI integrates properly with configuration."""
    logger.info("Testing CLI config integration...")

    try:
        from cli.app import MangaForgeApp

        app = MangaForgeApp()

        # Test config access
        download_dir = app.config.download_dir
        max_workers = app.config.max_chapter_workers
        default_format = app.config.default_format

        logger.info("✓ Config integration successful")
        logger.info(f"  Download dir: {download_dir}")
        logger.info(f"  Chapter workers: {max_workers}")
        logger.info(f"  Default format: {default_format}")

        return True

    except Exception as e:
        logger.error(f"Config integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all CLI tests."""
    logger.info("Starting MangaForge Phase 2 CLI system tests...")

    tests = [
        ("CLI Dependencies", test_cli_dependencies),
        ("CLI Imports", test_cli_imports),
        ("CLI Initialization", test_cli_initialization),
        ("Provider Integration", test_provider_integration),
        ("Config Integration", test_config_integration),
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
    logger.info("CLI TEST SUMMARY")
    logger.info(f"{'='*60}")

    passed = 0
    failed = 0

    for test_name, success in results:
        status = "PASSED" if success else "FAILED"
        logger.info(f"{test_name:25} : {status}")

        if success:
            passed += 1
        else:
            failed += 1

    logger.info(f"{'='*60}")
    logger.info(f"Total: {len(results)} tests")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")

    if failed == 0:
        logger.info("🎉 All CLI tests passed!")
        logger.info("✅ CLI system is ready for interactive testing")
        logger.info("\n💡 To test interactively:")
        logger.info("   python main.py")
        return 0
    else:
        logger.error("❌ Some CLI tests failed.")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)