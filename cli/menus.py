"""
Interactive menu functions for MangaForge CLI.

This module contains all the interactive menu logic including chapter
selection, download format selection, and settings management.
"""
import logging
from typing import List, Optional, TYPE_CHECKING
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

if TYPE_CHECKING:
    from models import Chapter, MangaInfo
    from core.config import Config

from .tables import display_chapters_table, display_manga_info_card, display_settings_table
from core.utils import parse_chapter_range

console = Console()
logger = logging.getLogger(__name__)


def display_manga_info(manga_info: 'MangaInfo') -> None:
    """
    Display manga information in a beautiful card format.

    Args:
        manga_info: MangaInfo object to display
    """
    display_manga_info_card(manga_info)


def select_chapters(chapters: List['Chapter'], config: 'Config') -> List['Chapter']:
    """
    Interactive chapter selection with pagination.

    Args:
        chapters: List of all available chapters
        config: Configuration object for pagination settings

    Returns:
        List of selected chapters
    """
    if not chapters:
        console.print("[red]No chapters available.[/red]")
        return []

    console.print(f"[bold cyan]📚 Total chapters available: {len(chapters)}[/bold cyan]\n")

    current_page = 1
    chapters_per_page = config.get('ui.chapters_per_page', 10)
    total_pages = (len(chapters) + chapters_per_page - 1) // chapters_per_page

    while True:
        # Calculate page boundaries
        start_idx = (current_page - 1) * chapters_per_page
        end_idx = min(start_idx + chapters_per_page, len(chapters))
        page_chapters = chapters[start_idx:end_idx]

        # Display chapters table with correct indices
        choice = display_chapters_table(chapters, current_page, total_pages, chapters_per_page)

        if choice == "N" and current_page < total_pages:
            current_page += 1
            continue
        elif choice == "P" and current_page > 1:
            current_page -= 1
            continue
        elif choice == "A":
            # Download all chapters
            console.print(f"[green]Selected all {len(chapters)} chapters[/green]")
            return chapters
        elif choice == "R":
            # Download range
            return select_chapter_range(chapters)
        elif choice == "S":
            # Select specific chapters
            return select_specific_chapters(chapters, current_page, chapters_per_page)
        elif choice == "Q":
            return []
        else:
            console.print("[red]Invalid choice.[/red]")


def select_chapter_range(chapters: List['Chapter']) -> List['Chapter']:
    """
    Select chapters by range input.

    Args:
        chapters: List of all available chapters

    Returns:
        List of selected chapters
    """
    console.print("\n[bold cyan]Range Selection:[/bold cyan]")
    console.print("Examples: 1-10, 15, 20-25, 1-5,10,15-20")

    while True:
        range_input = Prompt.ask("Enter chapter range").strip()

        if not range_input:
            console.print("[red]Range cannot be empty.[/red]")
            continue

        try:
            selected_chapters = parse_chapter_range(range_input, chapters)
            if selected_chapters:
                console.print(f"[green]Selected {len(selected_chapters)} chapters[/green]")
                return selected_chapters
            else:
                console.print("[yellow]No chapters matched the range.[/yellow]")
                if Confirm.ask("Try again?"):
                    continue
                return []
        except ValueError as e:
            console.print(f"[red]Invalid range format: {e}[/red]")
            if Confirm.ask("Try again?"):
                continue
            return []


def select_specific_chapters(chapters: List['Chapter'], current_page: int, chapters_per_page: int) -> List['Chapter']:
    """
    Select specific chapters by number.

    Args:
        chapters: List of all available chapters
        current_page: Current page number
        chapters_per_page: Chapters per page

    Returns:
        List of selected chapters
    """
    console.print("\n[bold cyan]Specific Chapter Selection:[/bold cyan]")
    console.print("Enter chapter numbers separated by commas (e.g., 1,3,5)")
    console.print(f"Current page shows chapters {((current_page-1)*chapters_per_page)+1}-{min(current_page*chapters_per_page, len(chapters))}")

    while True:
        selection_input = Prompt.ask("Enter chapter numbers").strip()

        if not selection_input:
            console.print("[red]Selection cannot be empty.[/red]")
            continue

        try:
            # Parse comma-separated numbers
            selected_indices = []
            for num_str in selection_input.split(','):
                num = int(num_str.strip())
                if num < 1:
                    raise ValueError(f"Chapter number must be >= 1: {num}")
                selected_indices.append(num - 1)  # Convert to 0-based index

            # Get selected chapters
            selected_chapters = []
            for idx in selected_indices:
                if 0 <= idx < len(chapters):
                    selected_chapters.append(chapters[idx])
                else:
                    console.print(f"[yellow]Chapter {idx+1} not found, skipping.[/yellow]")

            if selected_chapters:
                console.print(f"[green]Selected {len(selected_chapters)} chapters[/green]")
                return selected_chapters
            else:
                console.print("[yellow]No valid chapters selected.[/yellow]")
                if Confirm.ask("Try again?"):
                    continue
                return []

        except ValueError as e:
            console.print(f"[red]Invalid input: {e}[/red]")
            if Confirm.ask("Try again?"):
                continue
            return []


def select_download_format() -> Optional[str]:
    """
    Interactive download format selection.

    Returns:
        Selected format: 'images', 'cbz', 'pdf', 'both', or None if cancelled
    """
    console.print("\n[bold cyan]📥 Download Format:[/bold cyan]")

    format_table = Table(show_header=False, show_edge=False, pad_edge=False)
    format_table.add_column("Option", style="cyan", width=8)
    format_table.add_column("Format", style="white", width=15)
    format_table.add_column("Description", style="dim")

    format_table.add_row(
        "[1]",
        "Images Only",
        "Download images without conversion"
    )
    format_table.add_row(
        "[2]",
        "CBZ",
        "Convert to CBZ (+ delete images)"
    )
    format_table.add_row(
        "[3]",
        "PDF",
        "Convert to PDF (+ delete images)"
    )
    format_table.add_row(
        "[4]",
        "Both",
        "Create both CBZ & PDF (+ delete images)"
    )

    panel = Panel(
        format_table,
        title="[bold blue]Download Options[/bold blue]",
        border_style="blue",
        padding=(1, 2)
    )
    console.print(panel)

    while True:
        choice = Prompt.ask("Select format", choices=["1", "2", "3", "4"])

        format_map = {
            "1": "images",
            "2": "cbz",
            "3": "pdf",
            "4": "both"
        }

        selected_format = format_map.get(choice)
        if selected_format:
            console.print(f"[green]Selected format: {selected_format.upper()}[/green]")
            return selected_format

        console.print("[red]Invalid choice.[/red]")


def show_settings_menu(config: 'Config') -> None:
    """
    Display and manage settings menu.

    Args:
        config: Configuration object to modify
    """
    console.clear()
    console.print("[bold blue]⚙️ Settings Menu[/bold blue]\n")

    while True:
        display_settings_table(config)

        console.print("\n[bold cyan]Options:[/bold cyan]")
        console.print("[1] Change Download Directory")
        console.print("[2] Change Chapter Workers")
        console.print("[3] Change Image Workers")
        console.print("[4] Change Default Format")
        console.print("[5] Toggle Delete Images")
        console.print("[6] Change Image Quality")
        console.print("[0] Back to Main Menu")

        choice = Prompt.ask("\nSelect option", choices=["0", "1", "2", "3", "4", "5", "6"])

        if choice == "0":
            break
        elif choice == "1":
            change_download_directory(config)
        elif choice == "2":
            change_chapter_workers(config)
        elif choice == "3":
            change_image_workers(config)
        elif choice == "4":
            change_default_format(config)
        elif choice == "5":
            toggle_delete_images(config)
        elif choice == "6":
            change_image_quality(config)
        else:
            console.print("[red]Invalid choice.[/red]")


def change_download_directory(config: 'Config') -> None:
    """Change the download directory setting."""
    console.print(f"\n[bold]Current download directory:[/bold] {config.download_dir}")

    new_dir = Prompt.ask("Enter new download directory", default=str(config.download_dir))

    if new_dir != str(config.download_dir):
        try:
            new_path = Path(new_dir).expanduser().resolve()
            config.set('download.directory', str(new_path))
            config.save()
            console.print(f"[green]✓ Download directory changed to: {new_path}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to set directory: {e}[/red]")
    else:
        console.print("[yellow]Directory unchanged.[/yellow]")


def change_chapter_workers(config: 'Config') -> None:
    """Change the maximum chapter workers setting."""
    current = config.max_chapter_workers
    console.print(f"\n[bold]Current chapter workers:[/bold] {current}")

    while True:
        try:
            new_value = int(Prompt.ask("Enter new value (1-10)", default=str(current)))
            if 1 <= new_value <= 10:
                config.set('download.max_chapter_workers', new_value)
                config.save()
                console.print(f"[green]✓ Chapter workers changed to: {new_value}[/green]")
                break
            else:
                console.print("[red]Value must be between 1 and 10.[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")


def change_image_workers(config: 'Config') -> None:
    """Change the maximum image workers setting."""
    current = config.max_image_workers
    console.print(f"\n[bold]Current image workers:[/bold] {current}")

    while True:
        try:
            new_value = int(Prompt.ask("Enter new value (1-50)", default=str(current)))
            if 1 <= new_value <= 50:
                config.set('download.max_image_workers', new_value)
                config.save()
                console.print(f"[green]✓ Image workers changed to: {new_value}[/green]")
                break
            else:
                console.print("[red]Value must be between 1 and 50.[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")


def change_default_format(config: 'Config') -> None:
    """Change the default output format setting."""
    current = config.default_format
    console.print(f"\n[bold]Current default format:[/bold] {current.upper()}")

    format_options = {
        "1": "images",
        "2": "cbz",
        "3": "pdf"
    }

    console.print("\n[bold]Available formats:[/bold]")
    for key, value in format_options.items():
        marker = " [✓]" if value == current else " [ ]"
        console.print(f"[{key}] {value.upper()}{marker}")

    while True:
        choice = Prompt.ask("Select format", choices=["1", "2", "3"])

        new_format = format_options.get(choice)
        if new_format:
            config.set('output.default_format', new_format)
            config.save()
            console.print(f"[green]✓ Default format changed to: {new_format.upper()}[/green]")
            break
        else:
            console.print("[red]Invalid choice.[/red]")


def toggle_delete_images(config: 'Config') -> None:
    """Toggle the delete images after conversion setting."""
    current = config.delete_images_after
    console.print(f"\n[bold]Delete images after conversion:[/bold] {'Yes' if current else 'No'}")

    if Confirm.ask(f"Change to: {'No' if current else 'Yes'}?"):
        new_value = not current
        config.set('output.delete_images_after', new_value)
        config.save()
        console.print(f"[green]✓ Delete images set to: {'Yes' if new_value else 'No'}[/green]")


def change_image_quality(config: 'Config') -> None:
    """Change the image quality setting."""
    current = config.get('output.image_quality', 95)
    console.print(f"\n[bold]Current image quality:[/bold] {current}%")

    while True:
        try:
            new_value = int(Prompt.ask("Enter new quality (1-100)", default=str(current)))
            if 1 <= new_value <= 100:
                config.set('output.image_quality', new_value)
                config.save()
                console.print(f"[green]✓ Image quality changed to: {new_value}%[/green]")
                break
            else:
                console.print("[red]Quality must be between 1 and 100.[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number.[/red]")


def confirm_download(chapters: List['Chapter'], format_type: str) -> bool:
    """
    Confirm download with chapter and format details.

    Args:
        chapters: List of chapters to download
        format_type: Format to download as

    Returns:
        True if user confirms, False otherwise
    """
    total_chapters = len(chapters)

    console.print(f"\n[bold cyan]Download Summary:[/bold cyan]")
    console.print(f"Chapters: {total_chapters}")
    console.print(f"Format: {format_type.upper()}")

    # Show first few and last few chapters
    if total_chapters <= 5:
        chapter_list = "\n".join([f"  • {c.title}" for c in chapters])
    else:
        chapter_list = "\n".join([f"  • {c.title}" for c in chapters[:3]])
        chapter_list += f"\n  ... and {total_chapters - 3} more chapters"

    console.print(f"\n[bold]Chapters to download:[/bold]\n{chapter_list}")

    return Confirm.ask("\nProceed with download?")