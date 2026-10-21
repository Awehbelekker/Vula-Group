"""Railway startup script — reads PORT env var and starts uvicorn."""
import os
import subprocess
import sys

port = os.environ.get("PORT", "7438")
workers = os.environ.get("WEB_CONCURRENCY", "2")

cmd = [
    sys.executable, "-m", "uvicorn",
    "vula.api.server:app",
    "--host", "0.0.0.0",
    "--port", port,
    "--workers", workers,
    # Uvicorn's own lines to stdout, its real errors to stderr (vula/log_streams.py) — so Railway
    # stops labelling "Application startup complete" as an error.
    "--log-config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_config.json"),
]
print(f"Starting Vula API on port {port}", flush=True)
os.execvp(sys.executable, cmd)
