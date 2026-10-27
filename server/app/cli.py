"""`python -m app.cli` (TS Appendix F.7)."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _paths(fmt: str) -> int:
    from .core.config import get_config
    cfg = get_config()
    vals = {"data_dir": str(cfg.data_dir), "log_dir": str(cfg.log_dir), "host": cfg.host, "port": cfg.port,
            "scheme": cfg.scheme}
    if fmt == "json":
        print(json.dumps(vals))
    elif fmt == "cmd":  # Windows batch: for /f "delims=" %%L in ('python -m app.cli paths --format=cmd') do %%L
        print("\n".join(f'set "{k.upper()}={v}"' for k, v in vals.items()))
    else:
        import shlex
        print("\n".join(f"{k.upper()}={shlex.quote(str(v))}" for k, v in vals.items()))
    return 0


def _open_store():
    from .main import startup
    from .core.config import get_config
    return startup(get_config())


def _create_admin(user_id: str) -> int:
    from .auth.passwords import hash_password, random_password
    store, _ = _open_store()
    uid = user_id.casefold()
    pw = random_password(20)
    existing = store.fetchall_sync("SELECT id FROM users WHERE user_id = ?", [uid])
    if existing:
        store.write_sync(lambda c: c.execute("UPDATE users SET password_hash = ?, must_change_pw = true, role = 'SYSTEM_ADMIN', "
                                             "auth_source = 'LOCAL', is_active = true, failed_logins = 0, locked_until = NULL "
                                             "WHERE user_id = ?", [hash_password(pw), uid]))
    else:
        store.write_sync(lambda c: c.execute("INSERT INTO users(user_id, display_name, role, auth_source, password_hash, "
                                             "must_change_pw) VALUES (?, ?, 'SYSTEM_ADMIN', 'LOCAL', ?, true)",
                                             [uid, uid, hash_password(pw)]))
    from .services import audit
    audit.record_sync(store, action="USER_ADMIN", user_id="cli", context={"op": "create-admin", "target": uid})
    store.close()
    print(f"Admin '{uid}' one-time password: {pw}")
    return 0


def _rotate() -> int:
    from .core.config import get_config
    from .core.crypto import keyring
    cfg = get_config()
    store, _ = _open_store()
    ring = keyring()
    old_rows = {
        "connections": store.fetchall_sync("SELECT id, secret_ciphertext, secret_nonce, secret_key_version FROM connections"),
        "llm": store.fetchall_sync("SELECT id, api_key_ciphertext, api_key_nonce, api_key_version FROM llm_providers "
                                   "WHERE api_key_ciphertext IS NOT NULL"),
    }
    plain_c = [(str(r["id"]), ring.open(bytes(r["secret_ciphertext"]), bytes(r["secret_nonce"]), r["secret_key_version"],
                                        f"connections:{r['id']}:secret")) for r in old_rows["connections"]]
    plain_l = [(str(r["id"]), ring.open(bytes(r["api_key_ciphertext"]), bytes(r["api_key_nonce"]), r["api_key_version"],
                                        f"llm_providers:{r['id']}:api_key")) for r in old_rows["llm"]]
    v = ring.add_version(cfg.master_key_file)

    def unit(c):
        for cid, pt in plain_c:
            s = ring.seal(pt, f"connections:{cid}:secret")
            c.execute("UPDATE connections SET secret_ciphertext=?, secret_nonce=?, secret_key_version=? WHERE id=?",
                      [s.ciphertext, s.nonce, s.key_version, cid])
        for pid, pt in plain_l:
            s = ring.seal(pt, f"llm_providers:{pid}:api_key")
            c.execute("UPDATE llm_providers SET api_key_ciphertext=?, api_key_nonce=?, api_key_version=? WHERE id=?",
                      [s.ciphertext, s.nonce, s.key_version, pid])
    store.write_sync(unit)
    store.close()
    print(f"Re-encrypted {len(plain_c)} connection secrets and {len(plain_l)} API keys under key version {v}.")
    return 0


def _backup(to: str | None) -> int:
    from .jobs.tasks import do_backup
    store, _ = _open_store()
    res = do_backup(store, to, None, "cli")
    store.close()
    print(json.dumps(res, indent=2))
    return 0


def _verify() -> int:
    from .core.config import get_config
    from .services.audit import verify_chain
    store, _ = _open_store()
    res = verify_chain(store, get_config().log_dir)
    store.close()
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


def _doctor() -> int:
    import asyncio
    from .core.config import get_config
    from .services.llm import router
    cfg = get_config()
    store, _ = _open_store()
    report: dict = {"data_dir": str(cfg.data_dir), "log_dir": str(cfg.log_dir),
                    "disk_free_gb": round(shutil.disk_usage(cfg.data_dir).free / 1e9, 1), "extensions": store.extensions}
    if sys.platform != "win32":
        report["data_dir_mode"] = oct(cfg.data_dir.stat().st_mode & 0o777)
        report["key_file_mode"] = oct(cfg.master_key_file.stat().st_mode & 0o777) if cfg.master_key_file.exists() else None

    async def llm():
        out = {}
        for r in store.fetchall_sync("SELECT id, kind FROM llm_providers WHERE is_enabled"):
            p = router.provider_by_id(store, str(r["id"]))
            try:
                out[r["kind"]] = await p.test()
            finally:
                await p.aclose()
        return out
    report["llm"] = asyncio.run(llm())
    store.close()
    print(json.dumps(report, indent=2, default=str))
    return 0


def _seed_demo(path: str | None) -> int:
    from .demo import create_demo_db
    from .core.config import INSTALL_DIR
    target = Path(path) if path else INSTALL_DIR / "demo" / "demo_sales.duckdb"
    create_demo_db(target)
    print(f"Demo DuckDB written to {target}\nAdd it in Admin → Connections as engine DUCKDB with params.path = {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("paths")
    p.add_argument("--format", choices=["shell", "json", "cmd"], default="shell")
    p = sub.add_parser("start", help="start in the background (or --foreground for service managers)")
    p.add_argument("--foreground", "-Foreground", action="store_true")
    sub.add_parser("stop", help="graceful stop with forced fallback")
    sub.add_parser("status")
    p = sub.add_parser("create-admin")
    p.add_argument("--user-id", required=True)
    sub.add_parser("rotate-master-key")
    p = sub.add_parser("backup")
    p.add_argument("--to")
    sub.add_parser("verify-audit")
    sub.add_parser("doctor")
    p = sub.add_parser("seed-demo")
    p.add_argument("--path")
    a = ap.parse_args(argv)
    return {
        "paths": lambda: _paths(a.format),
        "start": lambda: __import__("app.control", fromlist=["start"]).start(a.foreground),
        "stop": lambda: __import__("app.control", fromlist=["stop"]).stop(),
        "status": lambda: __import__("app.control", fromlist=["status"]).status(),
        "create-admin": lambda: _create_admin(a.user_id),
        "rotate-master-key": _rotate,
        "backup": lambda: _backup(a.to),
        "verify-audit": _verify,
        "doctor": _doctor,
        "seed-demo": lambda: _seed_demo(a.path),
    }[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
