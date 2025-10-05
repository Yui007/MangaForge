#!/usr/bin/env python3
"""
MangaForge - Main Entry Point

A beautiful, interactive CLI manga downloader with plugin-based architecture.

Usage:
    python main.py

This will launch the interactive CLI interface where you can:
- Search for manga by title
- Download manga by URL
- Configure settings
- Manage downloads with live progress

Requirements:
    pip install -r requirements.txt

For more information, see README.md
"""
import logging
import sys
from pathlib import Path

# Add current directory to path for imports
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


def check_dependencies():
    """Check if required dependencies are installed."""
    required_modules = [
        ('rich', 'rich'),
        ('typer', 'typer'),
        ('questionary', 'questionary'),
        ('httpx', 'httpx'),
        ('bs4', 'beautifulsoup4'),  # BeautifulSoup4 imports as 'bs4'
        ('lxml', 'lxml'),
        ('yaml', 'PyYAML'),  # PyYAML imports as 'yaml'
        ('PIL', 'Pillow')  # Pillow imports as 'PIL'
    ]

    missing_modules = []

    for import_name, package_name in required_modules:
        try:
            __import__(import_name)
        except ImportError:
            missing_modules.append(package_name)

    if missing_modules:
        print("❌ Missing required dependencies:")
        for module in missing_modules:
            print(f"   • {module}")

        print("\n💡 Install with:")
        print(f"   pip install {' '.join(missing_modules)}")
        print("\n   Or install all requirements:")
        print("   pip install -r requirements.txt")
        return False

    return True


def main():
    """Main entry point for MangaForge."""
    print("🚀 Starting MangaForge...")

    # Check dependencies
    if not check_dependencies():
        return 1

    # Create logs directory if it doesn't exist
    (current_dir / 'logs').mkdir(exist_ok=True)

    # Set up file logging
    file_handler = logging.FileHandler(current_dir / 'logs' / 'mangaforge.log', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(file_handler)

    try:
        # Import and run the CLI application
        from cli.app import MangaForgeApp

        app = MangaForgeApp()
        app.run()

        return 0

    except KeyboardInterrupt:
        print("\n👋 MangaForge stopped by user")
        return 0
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
        print(f"\n❌ An unexpected error occurred: {e}")
        print("Check logs/mangaforge.log for details")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)