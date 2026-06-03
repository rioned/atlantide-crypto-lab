"""Gunicorn WSGI entry point — calls startup() on import, exposes Flask app."""
import os

# Startup the trading engine, strategy, WebSocket, and self-learning threads
from app.main import startup
startup()

# Expose Flask app for gunicorn
from app.routes import app
