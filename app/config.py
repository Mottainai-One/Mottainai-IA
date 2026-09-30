"""Compatibility shim: settings moved to `config.settings`."""
from config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
