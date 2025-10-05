"""
User input handlers for MangaForge CLI.

This module contains utility functions for getting and validating
user input with proper error handling and type conversion.
"""
import logging
from typing import Optional, Union, List
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt, FloatPrompt

console = Console()
logger = logging.getLogger(__name__)


def prompt_manga_title() -> str:
    """
    Prompt user for manga title with validation.

    Returns:
        Validated manga title string
    """
    while True:
        title = Prompt.ask("Enter manga title").strip()

        if not title:
            console.print("[red]❌ Manga title cannot be empty.[/red]")
            continue

        if len(title) < 2:
            console.print("[red]❌ Manga title must be at least 2 characters.[/red]")
            continue

        if len(title) > 100:
            console.print("[red]❌ Manga title must be less than 100 characters.[/red]")
            continue

        return title


def prompt_manga_url() -> str:
    """
    Prompt user for manga URL with validation.

    Returns:
        Validated manga URL string
    """
    while True:
        url = Prompt.ask("Enter manga URL").strip()

        if not url:
            console.print("[red]❌ URL cannot be empty.[/red]")
            continue

        # Basic URL validation
        if not (url.startswith('http://') or url.startswith('https://')):
            console.print("[red]❌ URL must start with http:// or https://[/red]")
            continue

        if len(url) < 10:
            console.print("[red]❌ URL seems too short.[/red]")
            continue

        if len(url) > 500:
            console.print("[red]❌ URL must be less than 500 characters.[/red]")
            continue

        return url


def prompt_chapter_range() -> str:
    """
    Prompt user for chapter range with examples.

    Returns:
        Chapter range string
    """
    console.print("\n[bold cyan]📖 Chapter Range Examples:[/bold cyan]")
    console.print("• 1-10 (chapters 1 through 10)")
    console.print("• 1,3,5 (specific chapters)")
    console.print("• 1-5,10,15-20 (mix of ranges and singles)")

    while True:
        range_input = Prompt.ask("Enter chapter range").strip()

        if not range_input:
            console.print("[red]❌ Range cannot be empty.[/red]")
            continue

        # Basic validation - check for valid characters
        import re
        if not re.match(r'^[\d\s\-,]+$', range_input):
            console.print("[red]❌ Range can only contain numbers, commas, and dashes.[/red]")
            continue

        return range_input


def prompt_positive_integer(prompt_text: str, default: Optional[int] = None, min_val: int = 1, max_val: int = 100) -> int:
    """
    Prompt user for a positive integer with validation.

    Args:
        prompt_text: Text to display for the prompt
        default: Default value if user enters nothing
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        Validated positive integer
    """
    while True:
        try:
            if default is not None:
                value = IntPrompt.ask(prompt_text, default=default)
            else:
                value = IntPrompt.ask(prompt_text)

            if value < min_val:
                console.print(f"[red]❌ Value must be at least {min_val}.[/red]")
                continue

            if value > max_val:
                console.print(f"[red]❌ Value must be at most {max_val}.[/red]")
                continue

            return value

        except ValueError:
            console.print("[red]❌ Please enter a valid number.[/red]")


def prompt_directory_path(prompt_text: str, default: Optional[Union[str, Path]] = None) -> Path:
    """
    Prompt user for a directory path with validation.

    Args:
        prompt_text: Text to display for the prompt
        default: Default path if user enters nothing

    Returns:
        Validated directory path
    """
    while True:
        if default is not None:
            default_str = str(default)
            path_str = Prompt.ask(prompt_text, default=default_str)
        else:
            path_str = Prompt.ask(prompt_text)

        if not path_str.strip():
            console.print("[red]❌ Directory path cannot be empty.[/red]")
            continue

        try:
            path = Path(path_str.strip()).expanduser().resolve()

            # Try to create directory if it doesn't exist
            path.mkdir(parents=True, exist_ok=True)

            return path

        except Exception as e:
            console.print(f"[red]❌ Invalid directory path: {e}[/red]")


def prompt_file_path(prompt_text: str, default: Optional[Union[str, Path]] = None, must_exist: bool = False) -> Path:
    """
    Prompt user for a file path with validation.

    Args:
        prompt_text: Text to display for the prompt
        default: Default path if user enters nothing
        must_exist: Whether the file must already exist

    Returns:
        Validated file path
    """
    while True:
        if default is not None:
            default_str = str(default)
            path_str = Prompt.ask(prompt_text, default=default_str)
        else:
            path_str = Prompt.ask(prompt_text)

        if not path_str.strip():
            console.print("[red]❌ File path cannot be empty.[/red]")
            continue

        try:
            path = Path(path_str.strip()).expanduser().resolve()

            if must_exist and not path.exists():
                console.print(f"[red]❌ File does not exist: {path}[/red]")
                continue

            if must_exist and not path.is_file():
                console.print(f"[red]❌ Path is not a file: {path}[/red]")
                continue

            return path

        except Exception as e:
            console.print(f"[red]❌ Invalid file path: {e}[/red]")


def prompt_choice(options: List[str], prompt_text: str, default: Optional[str] = None) -> str:
    """
    Prompt user to choose from a list of options.

    Args:
        options: List of valid options
        prompt_text: Text to display for the prompt
        default: Default choice if user enters nothing

    Returns:
        Selected option
    """
    if default and default in options:
        choice = Prompt.ask(prompt_text, choices=options, default=default)
    else:
        choice = Prompt.ask(prompt_text, choices=options)

    return choice


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """
    Prompt user with a yes/no question.

    Args:
        question: Question to ask
        default: Default answer if user just presses enter

    Returns:
        True if yes, False if no
    """
    return Confirm.ask(question, default=default)


def prompt_with_validation(prompt_text: str, validator_func, error_message: str = "Invalid input.", max_attempts: int = 3):
    """
    Prompt user with input validation.

    Args:
        prompt_text: Text to display for the prompt
        validator_func: Function that returns True if input is valid
        error_message: Message to show on validation failure
        max_attempts: Maximum number of attempts before giving up

    Returns:
        Validated user input
    """
    for attempt in range(max_attempts):
        user_input = Prompt.ask(prompt_text).strip()

        if validator_func(user_input):
            return user_input
        else:
            console.print(f"[red]❌ {error_message}[/red]")

            if attempt == max_attempts - 1:
                console.print(f"[red]❌ Maximum attempts ({max_attempts}) exceeded.[/red]")
                raise ValueError("Maximum validation attempts exceeded")

    # This should never be reached, but just in case
    raise ValueError("Unexpected validation error")


def display_input_hint(hint_text: str) -> None:
    """
    Display a hint for user input.

    Args:
        hint_text: Hint text to display
    """
    console.print(f"[dim cyan]💡 {hint_text}[/dim cyan]")


def display_warning(warning_text: str) -> None:
    """
    Display a warning message.

    Args:
        warning_text: Warning text to display
    """
    console.print(f"[yellow]⚠️  {warning_text}[/yellow]")


def display_error(error_text: str) -> None:
    """
    Display an error message.

    Args:
        error_text: Error text to display
    """
    console.print(f"[red]❌ {error_text}[/red]")


def display_success(success_text: str) -> None:
    """
    Display a success message.

    Args:
        success_text: Success text to display
    """
    console.print(f"[green]✅ {success_text}[/green]")


def display_info(info_text: str) -> None:
    """
    Display an info message.

    Args:
        info_text: Info text to display
    """
    console.print(f"[blue]ℹ️  {info_text}[/blue]")