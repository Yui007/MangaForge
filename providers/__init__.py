"""
Providers package for MangaForge.

This package contains all manga providers for the application.
Providers are automatically discovered and loaded by the ProviderManager.

AUTO-DISCOVERY MAGIC:
- Drop any provider file in this directory
- ProviderManager will automatically find and load it
- No manual registration required
- Each provider must inherit from BaseProvider

Example provider file structure:
providers/
  ├── __init__.py          # This file
  ├── mock.py             # Mock provider for testing
  ├── bato.py             # Bato provider
  ├── mangadex.py         # MangaDex provider
  └── comick.py           # Comick provider
"""
# This file enables auto-discovery of providers
# ProviderManager scans this directory for .py files and loads them

__all__ = []  # Providers register themselves automatically