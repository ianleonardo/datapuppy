"""Process control used by the Windows batch scripts (and usable on Linux): start, stop, status.

`python -m app.cli start [--foreground]` / `stop` / `status` implement the behaviour of TS Appendix F without
PowerShell or curl:
  * double-start prevention (PID file + liveness check),
  * background start with stdout/stderr captured in {log_dir}, console logs rotated (keep 5),
  * health polling of /healthz for up to 60 s (proxy bypassed, self-signed cert accepted on 127.0.0.1),
  * graceful stop via POST /internal/shutdown (localhost + token) so DuckDB checkpoints, 30 s wait,
    then forced termination as a fallback.
Exit codes: 0 OK, 1 failed, 2 already running / not running.
"""
from __future__ import annotations

import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .core.config import get_config
from .core.paths import ensure_tree
from .serve import _alive

SERVER_DIR = Path(__file__).resolve().parents[1]


def _opener(scheme: str):
    handlers: list = [urllib.request.ProxyHandler({})]  # never use a proxy for localhost
    if scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _rotate(log_dir: Path) -> None:
    for name in ("stdout", "stderr"):
        for i in (4, 3, 2, 1):
            src = log_dir / f"{name}.log.{i}"
            if src.exists():
                src.replace(log_dir / f"{name}.log.{i + 1}")
        cur = log_dir / f"{name}.log"
        if cur.exists():
            try:
                cur.replace(log_dir / f"{name}.log.1")
            except OSError:
                pass  # still open by a crashed process on Windows; append instead


def _healthy(cfg) -> bool:
    try:
        with _opener(cfg.scheme).open(f"{cfg.scheme}://127.0.0.1:{cfg.port}/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def status() -> int:
    cfg = get_config()
    pid_file = cfg.data_dir / "run" / "sqlassist.pid"
    pid = _read_pid(pid_file)
    if pid and _alive(pid):
        print(f"SQL Assistant is running (PID {pid}) at {cfg.scheme}://{cfg.host}:{cfg.port}"
              f" — {'healthy' if _healthy(cfg) else 'not responding'}.")
        return 0
    print("SQL Assistant is not running.")
    return 2


def start(foreground: bool = False) -> int:
    cfg = get_config()
    ensure_tree(cfg)
    run_dir = cfg.data_dir / "run"
    pid_file = run_dir / "sqlassist.pid"
    log_dir = cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    pid = _read_pid(pid_file)
    if pid and _alive(pid):
        print(f"SQL Assistant is already running (PID {pid}).")
        return 2
    pid_file.unlink(missing_ok=True)
    _rotate(log_dir)

    if foreground:  # for service managers (NSSM, Task Scheduler, systemd)
        from .serve import main as serve_main
        return serve_main()

    cmd = [sys.executable, "-m", "app.serve"]
    out = open(log_dir / "stdout.log", "ab")
    err = open(log_dir / "stderr.log", "ab")
    kwargs: dict = {"cwd": str(SERVER_DIR), "stdout": out, "stderr": err, "stdin": subprocess.DEVNULL,
                    "env": os.environ.copy()}
    if sys.platform == "win32":
        # detached + no console window, so closing this command prompt does not stop the server
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 — fixed argv, no shell
    out.close()
    err.close()
    pid_file.write_text(str(proc.pid))

    print(f"Starting SQL Assistant (PID {proc.pid})", end="", flush=True)
    for _ in range(60):
        if proc.poll() is not None:
            print()
            print(f"ERROR: process exited during startup. See {log_dir / 'stderr.log'} and {log_dir / 'app.log'}",
                  file=sys.stderr)
            try:  # show the reason right away
                tail = (log_dir / "stderr.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
                for line in tail:
                    print(f"  {line[:400]}", file=sys.stderr)
            except OSError:
                pass
            pid_file.unlink(missing_ok=True)
            return 1
        if _healthy(cfg):
            print()
            print(f"SQL Assistant is up: {cfg.scheme}://{cfg.host}:{cfg.port}  (data: {cfg.data_dir}, logs: {log_dir})")
            pw = run_dir / "initial-admin-password.txt"
            if pw.exists():
                print(f"First start: initial admin password is in {pw}")
            return 0
        print(".", end="", flush=True)
        time.sleep(1)
    print()
    print(f"WARNING: not healthy after 60 s; check {log_dir / 'app.log'}", file=sys.stderr)
    return 1


def stop() -> int:
    cfg = get_config()
    run_dir = cfg.data_dir / "run"
    pid_file = run_dir / "sqlassist.pid"
    token_file = run_dir / "shutdown.token"
    pid = _read_pid(pid_file)
    if not pid or not _alive(pid):
        print("SQL Assistant is not running.")
        pid_file.unlink(missing_ok=True)
        return 2

    # 1) graceful: shutdown endpoint (checkpoints DuckDB, drains jobs)
    sent = False
    if token_file.exists():
        req = urllib.request.Request(f"{cfg.scheme}://127.0.0.1:{cfg.port}/internal/shutdown", method="POST",
                                     headers={"X-Shutdown-Token": token_file.read_text().strip()}, data=b"")
        try:
            with _opener(cfg.scheme).open(req, timeout=5):
                sent = True
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: shutdown endpoint not reachable: {e}", file=sys.stderr)
    if not sent and sys.platform != "win32":
        os.kill(pid, signal.SIGTERM)  # SIGTERM also triggers graceful shutdown

    print(f"Stopping SQL Assistant (PID {pid})", end="", flush=True)
    for _ in range(30):
        if not _alive(pid):
            print()
            print("Stopped.")
            pid_file.unlink(missing_ok=True)
            return 0
        print(".", end="", flush=True)
        time.sleep(1)

    # 2) forced fallback
    print()
    print("WARNING: forcing stop. DuckDB will replay its WAL on next start.", file=sys.stderr)
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)  # noqa: S603,S607
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(5)
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    pid_file.unlink(missing_ok=True)
    print("Stopped.")
    return 0
