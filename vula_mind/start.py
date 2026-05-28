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
]
print(f"Starting Vula API on port {port}", flush=True)
os.execvp(sys.executable, cmd)
