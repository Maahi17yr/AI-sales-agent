"""Compatibility ASGI entrypoint for Render and local uvicorn commands."""
from application.api.server import app

__all__ = ["app"]
