"""Download the DuckDB fts / vss extensions matching the pinned DuckDB version (DD-14, TS §24.3).

Run on a machine with internet access, then copy the output folder to {data_dir}/extensions on the server.
The app disables auto-install, so extensions must be present locally; without them it degrades gracefully.
"""
import argparse
import pathlib
import duckdb

PLATFORM_MAP = {"manylinux2014_x86_64": "linux_amd64", "win_amd64": "windows_amd64", "macosx_11_0_arm64": "osx_arm64"}

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="extensions")
ap.add_argument("--platform", default=None, help="pip platform tag of the target host (defaults to this machine)")
a = ap.parse_args()
out = pathlib.Path(a.out).resolve()
out.mkdir(parents=True, exist_ok=True)
con = duckdb.connect(":memory:")
con.execute(f"SET extension_directory = '{out.as_posix()}'")
target = PLATFORM_MAP.get(a.platform or "", None)
for ext in ("fts", "vss"):
    if target:
        url = f"http://extensions.duckdb.org/v{duckdb.__version__}/{target}/{ext}.duckdb_extension.gz"
        print(f"For {target} download: {url} -> {out}/v{duckdb.__version__}/{target}/{ext}.duckdb_extension")
    else:
        con.execute(f"INSTALL {ext}")
        print(f"installed {ext} into {out}")
