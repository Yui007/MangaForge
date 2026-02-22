"""
Table formatting for MangaForge CLI.

This module handles all Rich table displays including search results,
chapter listings, and other tabular data with beautiful formatting.
"""
from typing import List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt

from models import MangaSearchResult, Chapter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import MangaInfo
    from core.config import Config

console = Console()


def display_search_results(results: List[MangaSearchResult], page: int, results_per_page: int = 10, has_next: bool = False) -> Optional[str]:
    """
    Display search results in a beautiful table with pagination.

    Args:
        results: List of manga search results to display (current page only)
        page: Current page number
        results_per_page: Number of results per page
        has_next: Whether there are more pages available

    Returns:
        User selection (manga number, N, P, Q)
    """
    # For pagination display, show current page and indicate if more pages exist
    if has_next:
        total_pages = f"{page}+"
    else:
        total_pages = str(page)

    # Since results contains only current page results, start from index 0
    page_results = results[:results_per_page]

    # Create results table
    table = Table(title=f"Search Results - Page {page}/{total_pages}", show_header=True, header_style="bold magenta")
    table.add_column("#", style="cyan", width=4, justify="center")
    table.add_column("Title", style="white", width=40, max_width=40)
    table.add_column("Provider", style="green", width=12, justify="center")
    table.add_column("URL", style="dim", width=30, max_width=30)

    for i, result in enumerate(page_results, 1):
        # Truncate long titles
        title = result.title
        if len(title) > 37:
            title = title[:34] + "..."

        table.add_row(
            str(i),
            title,
            result.provider_id.upper(),
            result.url[:27] + "..." if len(result.url) > 30 else result.url
        )

    # Display table in a panel
    panel = Panel(
        table,
        title="[bold blue]Search Results[/bold blue]",
        border_style="blue",
        padding=(0, 1)
    )
    console.print(panel)

    # Show navigation options
    console.print("\n[bold cyan]Navigation:[/bold cyan]")
    if has_next or page > 1:
        console.print(f"[N] Next Page  [P] Previous Page  [1-{len(page_results)}] Select Manga  [Q] Back")
    else:
        console.print(f"[1-{len(page_results)}] Select Manga  [Q] Back")

    # Get user input
    while True:
        choice = Prompt.ask("\nChoose option").strip().upper()

        if choice in ["N", "P", "Q"]:
            return choice
        elif choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(page_results):
                # Return the page-relative number since results contains only current page
                return str(num)
            else:
                console.print(f"[red]Please enter a number between 1 and {len(page_results)}[/red]")
        else:
            console.print("[red]Invalid choice. Please try again.[/red]")


def display_chapters_table(chapters: List[Chapter], page: int, total_pages: int, chapters_per_page: int = 10) -> Optional[str]:
    """
    Display chapters in a beautiful table with pagination.

    Args:
        chapters: List of chapters to display
        page: Current page number
        total_pages: Total number of pages
        chapters_per_page: Number of chapters per page

    Returns:
        User selection (N, P, A, R, S, Q)
    """
    import re

    # Calculate pagination
    start_idx = (page - 1) * chapters_per_page
    end_idx = min(start_idx + chapters_per_page, len(chapters))

    # Create chapters table
    table = Table(title=f"Chapters - Page {page}/{total_pages}", show_header=True, header_style="bold magenta")
    table.add_column("#", style="cyan", width=4, justify="center")
    table.add_column("Chapter", style="white", width=22, max_width=22)
    table.add_column("Vol", style="green", width=5, justify="center")
    table.add_column("Lang", style="yellow", width=5, justify="center")
    table.add_column("Scanlator", style="magenta", width=18, max_width=18)
    table.add_column("Date", style="dim", width=11, justify="center")

    for i in range(start_idx, end_idx):
        chapter = chapters[i]

        # Extract scanlator from title bracket notation [GroupName]
        title_text = chapter.title
        scanlator = "-"
        bracket_match = re.search(r'\[([^\]]+)\]\s*$', title_text)
        if bracket_match:
            scanlator = bracket_match.group(1)
            title_text = title_text[:bracket_match.start()].strip()
            if len(scanlator) > 16:
                scanlator = scanlator[:14] + ".."

        # Format chapter display
        if len(title_text) > 20:
            title_text = title_text[:18] + ".."

        # Format volume
        volume_display = chapter.volume if chapter.volume else "-"

        # Language
        lang_display = chapter.language.upper() if hasattr(chapter, 'language') and chapter.language else "-"

        # Format date
        if chapter.release_date:
            date_display = chapter.release_date[:10]
        else:
            date_display = "-"

        table.add_row(
            str(i + 1),
            title_text,
            volume_display,
            lang_display,
            scanlator,
            date_display
        )

    # Display table in a panel
    panel = Panel(
        table,
        title="[bold blue]Chapter List[/bold blue]",
        border_style="blue",
        padding=(0, 1)
    )
    console.print(panel)

    # Show navigation options
    console.print("\n[bold cyan]Options:[/bold cyan]")
    console.print("[N] Next  [P] Previous  [A] All  [R] Range  [S] Select  [Q] Back")

    # Get user input
    while True:
        choice = Prompt.ask("\nChoose option").strip().upper()

        if choice in ["N", "P", "A", "R", "S", "Q"]:
            return choice
        else:
            console.print("[red]Invalid choice. Please try again.[/red]")


def display_manga_info_card(manga_info: 'MangaInfo') -> None:
    """
    Display detailed manga information in a beautiful card format.

    Args:
        manga_info: MangaInfo object to display
    """
    # Create info table
    info_table = Table(show_header=False, show_edge=False, pad_edge=False)
    info_table.add_column("Field", style="cyan", width=12)
    info_table.add_column("Value", style="white")

    info_table.add_row("Title:", manga_info.title)

    if manga_info.alternative_titles:
        alt_titles = " | ".join(manga_info.alternative_titles[:2])  # Show first 2
        if len(manga_info.alternative_titles) > 2:
            alt_titles += f" (+{len(manga_info.alternative_titles) - 2} more)"
        info_table.add_row("Alt Titles:", alt_titles)

    if manga_info.authors:
        info_table.add_row("Author(s):", ", ".join(manga_info.authors))

    if manga_info.artists:
        info_table.add_row("Artist(s):", ", ".join(manga_info.artists))

    if manga_info.genres:
        genres_str = ", ".join(manga_info.genres[:5])  # Show first 5
        if len(manga_info.genres) > 5:
            genres_str += f" (+{len(manga_info.genres) - 5} more)"
        info_table.add_row("Genres:", genres_str)

    info_table.add_row("Status:", f"[bold]{manga_info.status}[/bold]")

    if manga_info.description:
        # Truncate long descriptions
        desc = manga_info.description
        if len(desc) > 200:
            desc = desc[:197] + "..."
        info_table.add_row("Description:", desc)

    # Create panel
    panel = Panel(
        info_table,
        title=f"[bold green]{manga_info.title}[/bold green]",
        border_style="green",
        padding=(1, 2)
    )
    console.print(panel)
    console.print()


def display_settings_table(config: 'Config') -> None:
    """
    Display current settings in a table format.

    Args:
        config: Configuration object to display
    """
    # Create settings table
    table = Table(title="Current Settings", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="cyan", width=25)
    table.add_column("Value", style="white", width=20)
    table.add_column("Description", style="dim", width=30)

    table.add_row(
        "Download Directory",
        config.download_dir.name,
        "Where manga chapters are saved"
    )
    table.add_row(
        "Chapter Workers",
        str(config.max_chapter_workers),
        "Parallel chapter downloads"
    )
    table.add_row(
        "Image Workers",
        str(config.max_image_workers),
        "Concurrent image downloads per chapter"
    )
    table.add_row(
        "Default Format",
        config.default_format.upper(),
        "Default output format (images/cbz/pdf)"
    )
    table.add_row(
        "Delete Images",
        "Yes" if config.delete_images_after else "No",
        "Delete images after conversion"
    )
    table.add_row(
        "Image Quality",
        f"{config.get('output.image_quality')}%",
        "JPEG quality for PDF conversion"
    )
    table.add_row(
        "Preferred Language",
        config.preferred_language.upper(),
        "Chapter language filter (e.g. EN)"
    )
    table.add_row(
        "Preferred Scanlator",
        config.preferred_scanlator or "(any)",
        "Preferred scanlation group"
    )

    # Display table in a panel
    panel = Panel(
        table,
        title="[bold blue]Settings[/bold blue]",
        border_style="blue",
        padding=(0, 1)
    )
    console.print(panel)
    console.print()


def display_download_progress(current: int, total: int, description: str) -> None:
    """
    Display download progress in a compact format.

    Args:
        current: Current progress count
        total: Total count
        description: Progress description
    """
    percentage = (current / total) * 100 if total > 0 else 0

    console.print(f"[green]✓[/green] {description}: {current}/{total} ({percentage:.1f}%)")


def display_success_message(message: str) -> None:
    """
    Display a success message in a highlighted box.

    Args:
        message: Success message to display
    """
    panel = Panel(
        f"[green]✓ {message}[/green]",
        style="green",
        padding=(0, 1)
    )
    console.print(panel)


def display_error_message(message: str) -> None:
    """
    Display an error message in a highlighted box.

    Args:
        message: Error message to display
    """
    panel = Panel(
        f"[red]✗ {message}[/red]",
        style="red",
        padding=(0, 1)
    )
    console.print(panel)


def display_info_message(message: str) -> None:
    """
    Display an info message in a highlighted box.

    Args:
        message: Info message to display
    """
    panel = Panel(
        f"[blue]ℹ {message}[/blue]",
        style="blue",
        padding=(0, 1)
    )
    console.print(panel)