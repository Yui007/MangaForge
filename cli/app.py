"""
Main CLI application for MangaForge.

This module contains the core CLI application that manages the interactive
manga downloader interface using Rich for beautiful output and Typer for
CLI framework functionality.
"""
import logging
import sys
from pathlib import Path
from typing import Optional, List, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, TaskID, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.live import Live

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.provider_manager import ProviderManager
from core.downloader import Downloader
from core.converter import Converter
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)
console = Console()

class MangaForgeApp:
    """
    Main MangaForge CLI application.

    This class manages the entire CLI experience including:
    - Main menu navigation
    - Search and URL flows
    - Chapter selection and downloading
    - Settings management
    - Progress display and user feedback
    """

    def __init__(self):
        """Initialize the MangaForge application."""
        self.config = Config()
        self.provider_manager = ProviderManager()
        self.downloader = Downloader(
            max_chapter_workers=self.config.max_chapter_workers,
            max_image_workers=self.config.max_image_workers
        )
        self.converter = Converter()

        # Progress tracking
        self.progress_tasks: dict[str, TaskID] = {}

        logger.info("MangaForge CLI initialized")

    def run(self):
        """Run the main application loop."""
        console.clear()
        self.show_header()

        while True:
            try:
                choice = self.show_main_menu()

                if choice == "1":
                    self.search_flow()
                elif choice == "2":
                    self.url_flow()
                elif choice == "3":
                    self.settings_flow()
                elif choice == "4":
                    self.exit_app()
                    break
                else:
                    console.print("[red]Invalid choice. Please try again.[/red]")

            except KeyboardInterrupt:
                console.print("\n[yellow]Operation cancelled by user.[/yellow]")
                if Confirm.ask("Return to main menu?"):
                    continue
                else:
                    self.exit_app()
                    break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                console.print(f"[red]An unexpected error occurred: {e}[/red]")
                if Confirm.ask("Return to main menu?"):
                    continue
                else:
                    break

    def show_header(self):
        """Display the application header."""
        header_text = Text("MangaForge v2.0", style="bold magenta", justify="center")
        header_panel = Panel(
            header_text,
            style="blue",
            padding=(1, 2)
        )
        console.print(header_panel)
        console.print()

    def show_main_menu(self) -> str:
        """Display the main menu and get user choice."""
        menu_text = """
[bold cyan]Main Menu:[/bold cyan]

[1] 🔍 Search Manga by Title
[2] 🔗 Get Manga by URL
[3] ⚙️  Settings
[4] 🚪 Exit

"""

        console.print(menu_text)
        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4"])
        return choice

    def search_flow(self):
        """Handle the search manga flow."""
        console.clear()
        console.print("[bold blue]🔍 Search Manga by Title[/bold blue]\n")

        # Get search query
        query = Prompt.ask("Enter manga title")
        if not query.strip():
            console.print("[red]Search query cannot be empty.[/red]")
            return

        # Show provider selection
        provider = self.select_provider()
        if not provider:
            return

        # Perform search with pagination
        current_page = 1
        while True:
            console.clear()
            console.print(f"[bold blue]🔍 Search Results for '{query}' - Page {current_page}[/bold blue]\n")

            try:
                results, has_next = provider.search(query, current_page)

                if not results:
                    console.print("[yellow]No results found.[/yellow]")
                    if Prompt.ask("Try a different search?"):
                        return self.search_flow()
                    return

                # Display results table
                from .tables import display_search_results
                results_per_page = self.config.get('ui.results_per_page', 10)
                selected = display_search_results(results, current_page, results_per_page, has_next)

                if selected == "N" and has_next:
                    current_page += 1
                    continue
                elif selected == "P" and current_page > 1:
                    current_page -= 1
                    continue
                elif selected == "Q":
                    return
                elif selected and selected.isdigit():
                    # User selected a manga
                    manga_index = int(selected) - 1
                    if 0 <= manga_index < len(results):
                        selected_manga = results[manga_index]
                        self.chapter_selection_flow(provider, selected_manga.manga_id)
                        return
                    else:
                        console.print("[red]Invalid selection.[/red]")
                        continue
                else:
                    console.print("[red]Invalid choice.[/red]")
                    continue

            except Exception as e:
                logger.error(f"Search failed: {e}")
                console.print(f"[red]Search failed: {e}[/red]")
                if Confirm.ask("Try again?"):
                    continue
                return

    def url_flow(self):
        """Handle the get manga by URL flow."""
        console.clear()
        console.print("[bold blue]🔗 Get Manga by URL[/bold blue]\n")

        url = Prompt.ask("Enter manga URL")
        if not url.strip():
            console.print("[red]URL cannot be empty.[/red]")
            return

        # Auto-detect provider
        provider = self.provider_manager.get_provider_from_url(url)
        if not provider:
            console.print("[red]Could not detect provider from URL.[/red]")
            console.print("Supported providers:", ", ".join(self.provider_manager.list_providers()))
            return

        console.print(f"[green]Detected provider: {provider.provider_name}[/green]")

        try:
            # Get manga info
            manga_info = provider.get_manga_info(url=url)
            console.print(f"[green]Found: {manga_info.title}[/green]")

            # Jump to chapter selection
            self.chapter_selection_flow(provider, manga_info.manga_id)

        except Exception as e:
            logger.error(f"URL flow failed: {e}")
            console.print(f"[red]Failed to get manga info: {e}[/red]")

    def select_provider(self) -> Optional[Any]:
        """Show provider selection menu."""
        providers = self.provider_manager.list_providers()

        if not providers:
            console.print("[red]No providers available.[/red]")
            return None

        console.print("[bold cyan]Select Provider:[/bold cyan]\n")

        for i, provider_id in enumerate(providers, 1):
            provider = self.provider_manager.get_provider(provider_id)
            console.print(f"[{i}] {provider.provider_name}")

        console.print(f"[0] Cancel\n")

        while True:
            choice = Prompt.ask("Choose provider", choices=[str(i) for i in range(len(providers) + 1)])

            if choice == "0":
                return None
            else:
                provider_id = providers[int(choice) - 1]
                return self.provider_manager.get_provider(provider_id)

    def chapter_selection_flow(self, provider: Any, manga_id: str):
        """Handle chapter selection and downloading."""
        try:
            # Get manga info
            manga_info = provider.get_manga_info(manga_id=manga_id)

            # Display manga info
            from .menus import display_manga_info
            display_manga_info(manga_info)

            # Get chapters with pagination
            all_chapters = provider.get_chapters(manga_id)
            if not all_chapters:
                console.print("[red]No chapters found.[/red]")
                return

            # Chapter selection
            from .menus import select_chapters
            selected_chapters = select_chapters(all_chapters, self.config)

            if not selected_chapters:
                console.print("[yellow]No chapters selected.[/yellow]")
                return

            # Download format selection
            from .menus import select_download_format
            download_format = select_download_format()

            if not download_format:
                return

            # Confirm download
            total_chapters = len(selected_chapters)
            console.print(f"\n[bold]Download {total_chapters} chapters as {download_format}?[/bold]")

            if not Confirm.ask("Proceed with download?"):
                return

            # Start download with progress
            self.download_with_progress(provider, manga_info, selected_chapters, download_format)

        except Exception as e:
            logger.error(f"Chapter selection flow failed: {e}")
            console.print(f"[red]Error: {e}[/red]")

    def download_with_progress(self, provider: Any, manga_info: MangaInfo, chapters: List[Chapter], format_type: str):
        """Download chapters with live progress display."""
        console.clear()
        console.print(f"[bold green]📥 Downloading: {manga_info.title}[/bold green]\n")

        # Create progress display
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
            refresh_per_second=4
        ) as progress:

            # Chapter download task
            chapter_task = progress.add_task(
                "Downloading chapters...",
                total=len(chapters)
            )

            # Image download task (will be updated per chapter)
            image_task = progress.add_task(
                "Downloading images...",
                total=100,
                visible=False
            )

            def progress_callback(completed: int, total: int, description: str):
                """Update progress bars."""
                if "Chapter" in description:
                    progress.update(chapter_task, completed=completed, description=description)
                else:
                    progress.update(image_task, completed=completed, total=total, description=description, visible=True)

            try:
                # Download chapters
                downloaded_paths = self.downloader.download_chapters(
                    provider,
                    manga_info,
                    chapters,
                    self.config.download_dir,
                    progress_callback
                )

                progress.update(chapter_task, completed=len(downloaded_paths))

                # Handle conversion if needed
                if format_type in ["cbz", "pdf", "both"]:
                    self.convert_with_progress(downloaded_paths, format_type, progress)

                # Success message
                console.print(f"\n[green]✅ Download complete![/green]")
                console.print(f"[green]Saved to: {self.config.download_dir}[/green]")

                if Confirm.ask("Return to main menu?"):
                    return
                else:
                    self.exit_app()

            except Exception as e:
                logger.error(f"Download failed: {e}")
                console.print(f"[red]❌ Download failed: {e}[/red]")

    def convert_with_progress(self, chapter_paths: List[Path], format_type: str, progress: Progress):
        """Convert downloaded chapters with progress display."""
        console.print(f"\n[bold yellow]🔄 Converting to {format_type.upper()}...[/bold yellow]")

        conversion_task = progress.add_task(
            "Converting chapters...",
            total=len(chapter_paths)
        )

        for i, chapter_path in enumerate(chapter_paths):
            try:
                # Handle "both" format carefully to avoid deleting images before PDF conversion
                if format_type == "both":
                    # For "both" format: create CBZ first but don't delete images yet
                    cbz_path = chapter_path.with_suffix('.cbz')
                    self.converter.to_cbz(chapter_path, cbz_path, delete_images=False)

                    # Create PDF second (images still exist)
                    pdf_path = chapter_path.with_suffix('.pdf')
                    self.converter.to_pdf(chapter_path, pdf_path, delete_images=False)

                    # Delete images only after both conversions are complete
                    if self.config.delete_images_after:
                        self.converter._cleanup_images(chapter_path, list(chapter_path.iterdir()))

                elif format_type == "cbz":
                    cbz_path = chapter_path.with_suffix('.cbz')
                    self.converter.to_cbz(chapter_path, cbz_path, self.config.delete_images_after)

                elif format_type == "pdf":
                    pdf_path = chapter_path.with_suffix('.pdf')
                    self.converter.to_pdf(chapter_path, pdf_path, self.config.delete_images_after)

                progress.update(conversion_task, completed=i + 1)

            except Exception as e:
                logger.error(f"Conversion failed for {chapter_path}: {e}")
                console.print(f"[red]Failed to convert {chapter_path.name}: {e}[/red]")

    def settings_flow(self):
        """Handle the settings menu."""
        from .menus import show_settings_menu
        show_settings_menu(self.config)

    def exit_app(self):
        """Clean exit from the application."""
        console.print("\n[bold cyan]Thank you for using MangaForge![/bold cyan]")
        console.print("[dim]Happy reading! 📚[/dim]")