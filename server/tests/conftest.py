from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def fake_llm_url():
    from tests.fake_llm import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture(scope="session")
def env(tmp_path_factory, fake_llm_url):
    root = tmp_path_factory.mktemp("sqa")
    from app.demo import create_demo_db

    demo = root / "demo" / "demo_sales.duckdb"
    create_demo_db(demo)
    cfg_file = root / "sqlassist.toml"
    cfg_file.write_text(f"""
[paths]
data_dir = "{(root / 'data').as_posix()}"
[security]
master_key_file = "{(root / 'master.key').as_posix()}"
[llm.spirare]
chat_url = "{fake_llm_url}/nlp/v1/chat/completions"
embed_url = "{fake_llm_url}/embedding/v1/embeddings"
api_key = "test-key-123"
""")
    from app.core.config import load_config
    cfg = load_config(cfg_file, env={})
    return {"cfg": cfg, "demo": demo, "root": root}


@pytest.fixture(scope="session")
def client(env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(env["cfg"])
    with TestClient(app) as c:
        from app.services.settings import settings
        settings._cache["ratelimit.login_per_min_ip"] = 1000  # tests log in far more than 20×/min
        yield c


class Session:
    def __init__(self, client, csrf: str):
        self.c = client
        self.h = {"X-CSRF-Token": csrf}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self.h, **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self.h, **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self.h, **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self.h, **kw)


def login(client, user, pw, tz="Asia/Singapore") -> Session:
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"user_id": user, "password": pw, "tz": tz})
    assert r.status_code == 200, r.text
    return Session(client, r.json()["csrf_token"])


def wait_job(s: Session, jid: str, timeout: float = 30) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = s.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job {jid} timed out")


def sse_events(text: str) -> list[tuple[str, str]]:
    out = []
    for block in text.split("\n\n"):
        ev, data = None, []
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
        if ev:
            out.append((ev, "\n".join(data)))
    return out


def admin_password(env) -> str:
    return (Path(env["cfg"].data_dir) / "run" / "initial-admin-password.txt").read_text().strip()
