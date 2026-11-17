"""Serve the compiled frontend and API for browser preview."""
import argparse

import uvicorn

from backend.local_access import LOOPBACK, SERVER_LIMITS
from backend.main import app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", choices=LOOPBACK)
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--strictPort", action="store_true")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, **SERVER_LIMITS)
