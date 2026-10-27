"""`python -m app.serve` — runs Uvicorn with ONE worker (DD-11), writes the PID file, handles signals (TS F.7)."""
from __future__ import annotations

import asyncio
import os
import signal
import sys

import uvicorn

from .core.config import get_config
from .core.paths import ensure_tree, write_private
from .main import State, create_app


def main() -> int:
    cfg = get_config()
    ensure_tree(cfg)
    pid_file = cfg.data_dir / "run" / "sqlassist.pid"
    if pid_file.exists():
        try:
            old = int(pid_file.read_text().strip())
            if old != os.getpid() and _alive(old):
                print(f"SQL Assistant is already running (PID {old}).", file=sys.stderr)
                return 2
        except ValueError:
            pass
    write_private(pid_file, str(os.getpid()))
    app = create_app(cfg)
    ssl_cert, ssl_key = cfg.get("server.ssl_certfile") or None, cfg.get("server.ssl_keyfile") or None
    if not (ssl_cert and ssl_key) and not cfg.get("server.allow_insecure_http"):
        print("Refusing plain HTTP. In " + str(cfg.config_file or "config/sqlassist.toml") + " either set [server] "
              "ssl_certfile/ssl_keyfile for HTTPS, or set allow_insecure_http = true (and host = \"127.0.0.1\" "
              "for a local-only pilot).", file=sys.stderr)
        return 1
    config = uvicorn.Config(app, host=cfg.host, port=cfg.port, workers=1, ssl_certfile=ssl_cert, ssl_keyfile=ssl_key,
                            proxy_headers=bool(cfg.get("server.proxy_headers")), log_config=None, access_log=False,
                            timeout_graceful_shutdown=30)
    server = uvicorn.Server(config)

    async def run():
        serve_task = asyncio.create_task(server.serve())
        while State.shutdown_event is None and not serve_task.done():
            await asyncio.sleep(0.1)
        if State.shutdown_event is not None:
            waiter = asyncio.create_task(State.shutdown_event.wait())
            done, _ = await asyncio.wait({serve_task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if waiter in done:
                server.should_exit = True
            await serve_task

    if sys.platform == "win32":  # console-control handlers
        try:
            signal.signal(signal.SIGBREAK, lambda *_: setattr(server, "should_exit", True))  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    try:
        asyncio.run(run())
    finally:
        try:
            if pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
    return 0


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wintypes.DWORD()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(main())
