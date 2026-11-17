"""Single process serves API, WebSocket, UI and the persistent report scheduler."""
import os
from pathlib import Path
import uvicorn

if __name__ == "__main__":
    # Mounted Railway volumes start root-owned. Initialize only the data directory,
    # then run the web process without root privileges.
    if os.getenv("PULSE_ACCESS") == "cloud" and hasattr(os, "getuid") and os.getuid() == 0:
        database = Path(os.getenv("PULSE_DB", "/data/market.db")).resolve()
        folder = database.parent
        folder.mkdir(parents=True, exist_ok=True)
        os.chown(folder, 10001, 10001)
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(database) + suffix)
            if path.is_file() and not path.is_symlink():
                os.chown(path, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
        os.umask(0o077)
    uvicorn.run("backend.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8765")),
                workers=1, proxy_headers=False, limit_concurrency=80, timeout_keep_alive=5,
                ws_max_size=4096, ws_max_queue=8, ws_per_message_deflate=False)
