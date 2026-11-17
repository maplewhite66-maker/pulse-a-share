import argparse
import os
import threading
import time
import webbrowser

import uvicorn

from backend.local_access import LOOPBACK, SERVER_LIMITS
from backend.main import app


def main():
    parser = argparse.ArgumentParser(description="Launch Pulse locally.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host", default="127.0.0.1", choices=LOOPBACK)
    parser.add_argument("--port", default=int(os.getenv("PULSE_PORT", "8765")), type=int)
    args = parser.parse_args()
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, **SERVER_LIMITS))

    def open_when_ready():
        for _ in range(150):
            if server.started:
                host = "[::1]" if args.host == "::1" else args.host
                webbrowser.open(f"http://{host}:{args.port}/")
                return
            time.sleep(.1)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()
    return 0 if server.started else 1


if __name__ == "__main__":
    raise SystemExit(main())
