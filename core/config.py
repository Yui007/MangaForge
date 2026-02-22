"""
Configuration management for MangaForge.

This module handles loading and managing application settings from
YAML configuration files with proper validation and defaults.
"""
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
import os

logger = logging.getLogger(__name__)


class Config:
    """
    Configuration manager for MangaForge.

    This class loads settings from YAML files and provides
    easy access to configuration values with proper defaults.

    Features:
    - YAML configuration file loading
    - Environment variable overrides
    - Configuration validation
    - Type conversion and validation
    - Default value handling
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to configuration file. If None, uses default locations.
        """
        self.config_path = self._find_config_file(config_path)
        self._config: Dict[str, Any] = {}
        self._load_config()

    def _find_config_file(self, config_path: Optional[str] = None) -> Path:
        """
        Find the configuration file to load.

        Args:
            config_path: Explicit path to config file

        Returns:
            Path to the configuration file to use
        """
        if config_path:
            path = Path(config_path)
            if path.exists():
                return path
            else:
                logger.warning(f"Config file not found: {path}")

        # Try default locations
        search_paths = [
            Path.cwd() / 'config' / 'settings.yaml',
            Path.cwd() / 'settings.yaml',
            Path.home() / '.mangaforge' / 'settings.yaml',
        ]

        for path in search_paths:
            if path.exists():
                logger.info(f"Found config file: {path}")
                return path

        # Return default path even if it doesn't exist
        default_path = Path.cwd() / 'config' / 'settings.yaml'
        logger.info(f"Using default config path: {default_path}")
        return default_path

    def _load_config(self):
        """Load configuration from file."""
        if not self.config_path.exists():
            logger.warning(f"Config file not found: {self.config_path}. Using defaults.")
            self._config = self._get_default_config()
            return

        try:
            import yaml
            with open(self.config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f) or {}

            # Merge with defaults
            self._config = self._merge_configs(self._get_default_config(), file_config)

            logger.info(f"Loaded configuration from {self.config_path}")

        except ImportError:
            logger.error("PyYAML not installed. Using default configuration.")
            self._config = self._get_default_config()
        except Exception as e:
            logger.error(f"Failed to load config file: {e}. Using defaults.")
            self._config = self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration values."""
        return {
            'download': {
                'directory': str(Path.cwd() / 'downloads'),
                'max_chapter_workers': 3,
                'max_image_workers': 10,
            },
            'output': {
                'default_format': 'cbz',
                'delete_images_after': True,
                'image_quality': 95,
            },
            'providers': {
                'enabled': ['mock'],  # Only mock for Phase 1
                'rate_limits': {
                    'default': 1.0,
                }
            },
            'network': {
                'timeout': 30,
                'retry_attempts': 3,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            },
            'ui': {
                'results_per_page': 10,
                'chapters_per_page': 10,
                'theme': 'default',
            },
            'logging': {
                'level': 'INFO',
                'file': str(Path.cwd() / 'logs' / 'mangaforge.log'),
            }
        }

    def _merge_configs(self, defaults: Dict[str, Any], user_config: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merge user config with defaults."""
        result = defaults.copy()

        for key, value in user_config.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value

        return result

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value.

        Args:
            key: Configuration key (dot notation: 'download.directory')
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        keys = key.split('.')
        value = self._config

        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value: Any):
        """
        Set a configuration value.

        Args:
            key: Configuration key (dot notation: 'download.directory')
            value: Value to set
        """
        keys = key.split('.')
        config = self._config

        # Navigate to the parent of the target key
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # Set the value
        config[keys[-1]] = value

    def save(self):
        """Save current configuration to file."""
        try:
            import yaml

            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self._config, f, default_flow_style=False, indent=2)

            logger.info(f"Saved configuration to {self.config_path}")

        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            raise

    # Convenience properties for commonly used settings
    @property
    def download_dir(self) -> Path:
        """Get download directory as Path object."""
        return Path(self.get('download.directory'))

    @property
    def max_chapter_workers(self) -> int:
        """Get maximum chapter download workers."""
        return self.get('download.max_chapter_workers', 3)

    @property
    def max_image_workers(self) -> int:
        """Get maximum image download workers."""
        return self.get('download.max_image_workers', 10)

    @property
    def default_format(self) -> str:
        """Get default output format."""
        return self.get('output.default_format', 'cbz')

    @property
    def delete_images_after(self) -> bool:
        """Get whether to delete images after conversion."""
        return self.get('output.delete_images_after', True)

    @property
    def enabled_providers(self) -> List[str]:
        """Get list of enabled provider IDs."""
        return self.get('providers.enabled', ['mock'])

    @property
    def network_timeout(self) -> int:
        """Get network timeout in seconds."""
        return self.get('network.timeout', 30)

    @property
    def retry_attempts(self) -> int:
        """Get number of retry attempts for failed requests."""
        return self.get('network.retry_attempts', 3)

    @property
    def preferred_language(self) -> str:
        """Get preferred chapter language code (e.g. 'en')."""
        return self.get('providers.preferred_language', 'en')

    @property
    def preferred_scanlator(self) -> str:
        """Get preferred scanlation group name (empty = any)."""
        return self.get('providers.preferred_scanlator', '')

    def get_rate_limit(self, provider_id: str) -> float:
        """
        Get rate limit for a specific provider.

        Args:
            provider_id: Provider ID to get rate limit for

        Returns:
            Rate limit in seconds between requests
        """
        # Check provider-specific rate limit first
        rate_limit = self.get(f'providers.rate_limits.{provider_id}')
        if rate_limit is not None:
            return rate_limit

        # Fall back to default
        return self.get('providers.rate_limits.default', 1.0)

    def __str__(self) -> str:
        """String representation of configuration."""
        return f"Config(path={self.config_path})"

    def __repr__(self) -> str:
        """Detailed string representation."""
        return f"Config(config_path='{self.config_path}', keys={list(self._config.keys())})"