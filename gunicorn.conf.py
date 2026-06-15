"""Gunicorn configuration for the web dashboard.

Railway (and Heroku, etc.) inject the listening port via the ``PORT``
environment variable. Reading it here — instead of passing
``--bind 0.0.0.0:$PORT`` on the command line — means the start command does
NOT depend on shell variable expansion. That avoids the
"'$PORT' is not a valid port number" error you get when the platform runs the
start command without a shell (the literal text ``$PORT`` reaches gunicorn).

Start the server with:  gunicorn web:app -c gunicorn.conf.py
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = 2
timeout = 120  # an analysis can take up to ~60s (API calls + optional AI)
