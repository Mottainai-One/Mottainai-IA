"""Compatibility shim: the FastAPI app moved to `interfaces.api.main`."""
from interfaces.api.main import *  # noqa: F403
from interfaces.api.main import app

__all__ = ["app"]
