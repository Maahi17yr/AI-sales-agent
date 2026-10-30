"""Temporary deployment compatibility entrypoint.

Render's existing service is configured to start agui_server:app.
The real application now lives in application.api.server.
Update Render's start command to application.api.server:app when convenient.
"""
from application.api.server import app
