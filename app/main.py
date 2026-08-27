"""Compatibilidade: a interface FastAPI foi movida para `interfaces.api.main`."""
from interfaces.api.main import *  # noqa: F403
from interfaces.api.main import app

__all__ = ["app"]
