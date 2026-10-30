"""Temporary deployment compatibility entrypoint.

Render's existing service is configured to start agui_server:app.
The real application now lives in core.api.server.
Update Render's start command to core.api.server:app when convenient.
"""
from core.api.server import app
