"""A tiny fake OpenAI-compatible (Spirare-style) gateway for tests and offline demos.

Run: python -m tests.fake_llm --port 9911
It answers chat completions with schema-aware JSON and returns deterministic 1024-d embeddings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re

from fastapi import FastAPI, Request

app = FastAPI()
CALLS: list[dict] = []


def _vec(text: str, dim: int = 1024) -> list[float]:
    words = re.findall(r"[a-z_]+", text.lower())
    v = [0.0] * dim
    for w in words:
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _answer(system: str, user: str) -> dict:
    s, u = system.lower(), user.lower()
    if "read-only" in s and "<schema>" in s:  # GENERATE_SQL
        if "month" in u or "growth" in u:
            sql = ("WITH m AS (SELECT date_trunc('month', o.order_date) AS month, sum(p.amount) AS revenue FROM orders o "
                   "JOIN payments p ON p.order_id = o.order_id WHERE o.country_code = 'US' GROUP BY 1) SELECT month, revenue, "
                   "revenue / lag(revenue) OVER (ORDER BY month) - 1 AS mom_growth FROM m ORDER BY month")
        elif "all orders" in u or "every order" in u:
            sql = "SELECT * FROM orders"
        else:
            sql = "SELECT o.order_id, o.order_date, o.status, p.amount FROM orders o JOIN payments p ON p.order_id = o.order_id WHERE o.country_code = 'SG' ORDER BY o.order_date DESC LIMIT 50"
        return {"reply": "Got it! Here's a draft — want me to run it, or should we sniff out some changes first?",
                "sql": sql, "dialect": "DUCKDB", "tables_used": ["orders", "payments"],
                "assumptions": ["Revenue = settled payment amount"], "mood": "happy"}
    if "insights" in u or "result profile" in u:
        return {"reply": "Nice haul! Here's what jumps out.", "insights": ["Revenue is concentrated in a few months.",
                                                                          "Completed orders dominate."],
                "anomalies": [], "next_steps": [{"label": "Split by channel", "prompt": "Break this down by channel"}],
                "mood": "excited"}
    if "review this" in s:
        return {"reply": "Looks mostly fine!", "issues": [], "mood": "happy"}
    if "explain the database structure" in s:
        return {"reply": "Orders sit in the middle: customers place them, payments settle them.", "tables": [], "mood": "happy"}
    return {"reply": "pong" if "pong" in u else "Hey friend! Ready when you are.", "mood": "happy"}


@app.post("/nlp/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    CALLS.append({"kind": "chat", "auth": req.headers.get("authorization"), "body": body})
    msgs = body["messages"]
    system = "\n".join(m["content"] for m in msgs if m["role"] == "system")
    user = [m["content"] for m in msgs if m["role"] == "user"][-1]
    content = json.dumps(_answer(system, user)) if body.get("response_format") or "json" in system.lower() else "pong"
    return {"choices": [{"message": {"role": "assistant", "content": "<think>hidden</think>" + content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


@app.post("/embedding/v1/embeddings")
async def emb(req: Request):
    body = await req.json()
    CALLS.append({"kind": "embed", "n": len(body["input"])})
    return {"data": [{"index": i, "embedding": _vec(t)} for i, t in enumerate(body["input"])]}


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9911)
    a = ap.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
