"""
Provider manager for MangaForge.

This module handles auto-discovery and management of all manga providers.
It automatically finds and loads all provider classes from the providers/
directory without manual registration.

NO MANUAL REGISTRATION NEEDED - Just drop a provider file in /providers/
and it's automatically loaded.
"""
import importlib
import inspect
import logging
from typing import Dict, List, Optional
from pathlib import Path

from .base_provider import BaseProvider, ProviderError

logger = logging.getLogger(__name__)


class ProviderManager:
    """
    Auto-discovers and manages all manga providers.

    This class automatically scans the providers/ directory for any
    classes that inherit from BaseProvider and makes them available
    for use. No manual registration is required.

    Features:
    - Auto-discovery of providers from /providers/ folder
    - Provider validation and error handling
    - Provider lookup by ID or URL
    - Provider listing and metadata
    """

    def __init__(self):
        """Initialize the provider manager and auto-discover providers."""
        self.providers: Dict[str, BaseProvider] = {}
        self._auto_discover_providers()
        logger.info(f"Loaded {len(self.providers)} providers: {list(self.providers.keys())}")

    def _auto_discover_providers(self):
        """
        Automatically find and load all provider classes.

        Scans the providers/ directory for Python files, imports them,
        and looks for classes that inherit from BaseProvider.
        """
        providers_dir = Path(__file__).parent.parent / 'providers'

        if not providers_dir.exists():
            logger.warning(f"Providers directory not found: {providers_dir}")
            return

        # Scan all Python files in providers directory
        for provider_file in providers_dir.glob('*.py'):
            if provider_file.name == '__init__.py':
                continue

            try:
                # Import the module
                module_name = f"providers.{provider_file.stem}"
                module = importlib.import_module(module_name)

                # Find all classes in the module that inherit from BaseProvider
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if (issubclass(obj, BaseProvider) and
                        obj != BaseProvider and
                        hasattr(obj, 'provider_id') and
                        obj.provider_id):

                        # Check if provider_id is already registered
                        if obj.provider_id in self.providers:
                            logger.warning(f"Duplicate provider ID '{obj.provider_id}' found in {provider_file.name}. Skipping.")
                            continue

                        # Instantiate the provider
                        try:
                            provider_instance = obj()
                            self.providers[obj.provider_id] = provider_instance
                            logger.debug(f"Loaded provider: {obj.provider_name} ({obj.provider_id})")
                        except Exception as e:
                            logger.error(f"Failed to initialize provider {name} from {provider_file.name}: {e}")

            except Exception as e:
                logger.error(f"Failed to import provider module {provider_file.name}: {e}")

    def get_provider(self, provider_id: str) -> BaseProvider:
        """
        Get a provider instance by ID.

        Args:
            provider_id: The unique identifier for the provider

        Returns:
            BaseProvider instance

        Raises:
            ProviderError: If provider is not found
        """
        if provider_id not in self.providers:
            available = ', '.join(self.providers.keys())
            raise ProviderError(f"Provider '{provider_id}' not found. Available providers: {available}")

        return self.providers[provider_id]

    def list_providers(self) -> List[str]:
        """
        List all available provider IDs.

        Returns:
            List of provider ID strings
        """
        return list(self.providers.keys())

    def get_provider_from_url(self, url: str) -> Optional[BaseProvider]:
        """
        Detect provider from URL.

        Args:
            url: URL to analyze

        Returns:
            BaseProvider instance if URL matches a provider, None otherwise
        """
        for provider in self.providers.values():
            if provider.base_url in url:
                return provider
        return None

    def get_provider_info(self, provider_id: str) -> Optional[Dict]:
        """
        Get detailed information about a provider.

        Args:
            provider_id: The provider ID to get info for

        Returns:
            Dictionary with provider information or None if not found
        """
        if provider_id not in self.providers:
            return None

        provider = self.providers[provider_id]
        return {
            'id': provider.provider_id,
            'name': provider.provider_name,
            'base_url': provider.base_url,
            'class': provider.__class__.__name__
        }

    def reload_providers(self):
        """
        Reload all providers (useful for development).

        This will re-scan the providers directory and reload all providers.
        """
        logger.info("Reloading providers...")
        self.providers.clear()
        self._auto_discover_providers()
        logger.info(f"Reloaded {len(self.providers)} providers")

    def validate_provider(self, provider_id: str) -> bool:
        """
        Validate that a provider is working correctly.

        Args:
            provider_id: The provider ID to validate

        Returns:
            True if provider is working, False otherwise
        """
        try:
            provider = self.get_provider(provider_id)

            # Try a simple test search
            results, has_next = provider.search("test", page=1)

            # If we get here without exceptions, provider is working
            logger.info(f"Provider '{provider_id}' validation successful")
            return True

        except Exception as e:
            logger.error(f"Provider '{provider_id}' validation failed: {e}")
            return False

    def __len__(self) -> int:
        """Return the number of loaded providers."""
        return len(self.providers)

    def __contains__(self, provider_id: str) -> bool:
        """Check if a provider ID is loaded."""
        return provider_id in self.providers

    def __iter__(self):
        """Iterate over all providers."""
        return iter(self.providers.values())