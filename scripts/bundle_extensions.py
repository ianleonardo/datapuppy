"""Download the DuckDB fts / vss extensions for offline use (DD-14, TS §24.3).

No duckdb import needed: the DuckDB version comes from server/requirements.lock, and the extension files are fetched
directly from extensions.duckdb.org (or a mirror) into the layout DuckDB expects:
    <out>/v<version>/<platform>/<ext>.duckdb_extension
Copy <out> to {data_dir}/extensions on the server (the Docker image seeds it automatically).
The app never auto-installs extensions; without them it falls back to slower keyword / exact vector search.

Usage: python scripts/bundle_extensions.py [--out extensions] [--platform manylinux2014_x86_64|win_amd64|linux_amd64|...]
                                           [--version 1.4.1] [--base-url https://extensions.duckdb.org]
Exit code 0 when every extension was bundled, 1 otherwise (the release build treats this as a warning).
"""
from __future__ import annotations

import argparse
import gzip
import os
import platform
import re
import shutil
import ssl
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = ("fts", "vss")


def duckdb_platform(tag: str | None) -> str:
    """pip platform tag (or DuckDB name) of the target → DuckDB platform name; None = this machine."""
    if not tag:
        system, machine = platform.system().lower(), platform.machine().lower()
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        return {"darwin": f"osx_{arch}", "windows": f"windows_{arch}"}.get(system, f"linux_{arch}")
    t = tag.lower()
    if re.fullmatch(r"(linux|osx|windows)_(amd64|arm64)(_gcc4)?", t):
        return t                                        # already a DuckDB platform name
    arch = "arm64" if ("arm64" in t or "aarch64" in t) else "amd64"
    if t.startswith(("manylinux", "musllinux", "linux")):
        return f"linux_{arch}"
    if t.startswith("win"):
        return f"windows_{arch}"
    if t.startswith("macosx"):
        return f"osx_{arch}"
    raise SystemExit(f"Unknown platform tag: {tag}")


def pinned_version() -> str:
    lock = ROOT / "server" / "requirements.lock"
    m = re.search(r"^duckdb==([0-9][0-9.]*)\s*$", lock.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"duckdb is not pinned in {lock}")
    return m.group(1)


def ssl_context() -> ssl.SSLContext:
    """Prefer certifi's CA bundle: python.org builds on macOS often have no system CA certificates configured."""
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url: str, dest: Path, ctx: ssl.SSLContext) -> None:
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))  # honours HTTP(S)_PROXY
    req = urllib.request.Request(url, headers={"User-Agent": "sqlassist-bundle-extensions"})
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=dest.parent, suffix=".part")
    try:
        with opener.open(req, timeout=60) as resp, tmp:
            if url.endswith(".gz"):
                with gzip.GzipFile(fileobj=resp) as gz:
                    shutil.copyfileobj(gz, tmp)
            else:
                shutil.copyfileobj(resp, tmp)
        os.replace(tmp.name, dest)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "extensions"))
    ap.add_argument("--platform", default=None, help="target platform (pip tag or DuckDB name); default: this machine")
    ap.add_argument("--version", default=None, help="DuckDB version; default: the one pinned in requirements.lock")
    ap.add_argument("--base-url", default=os.environ.get("DUCKDB_EXTENSIONS_URL", "https://extensions.duckdb.org"))
    a = ap.parse_args(argv)

    version = (a.version or pinned_version()).lstrip("v")
    target = duckdb_platform(a.platform)
    folder = Path(a.out).resolve() / f"v{version}" / target
    folder.mkdir(parents=True, exist_ok=True)
    ctx = ssl_context()
    failed = []
    for ext in EXTENSIONS:
        dest = folder / f"{ext}.duckdb_extension"
        url = f"{a.base_url.rstrip('/')}/v{version}/{target}/{ext}.duckdb_extension.gz"
        try:
            fetch(url, dest, ctx)
            print(f"  {ext}: {dest.stat().st_size // 1024} KB  ({target}, DuckDB v{version})")
        except Exception as e:  # noqa: BLE001
            failed.append(ext)
            print(f"  WARNING: could not download {ext} from {url}: {e}", file=sys.stderr)
    if failed:
        print("  The app still works without them (keyword and vector search use slower fallbacks). Download the files"
              f" above on a machine with internet access and put them in {folder}, or set DUCKDB_EXTENSIONS_URL to a"
              " mirror.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
