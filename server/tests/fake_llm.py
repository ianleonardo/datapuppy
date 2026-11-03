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


def _answer(system: str, user: str, all_user: str = "") -> dict:
    s, u, au = system.lower(), user.lower(), all_user.lower()
    if "read-only" in s and "<schema>" in s and "catalog probe" in au:  # tests: ask for a table's columns first
        if "<more_schema>" not in u:
            return {"reply": "Let me look at that table.", "sql": "", "need_tables": ["COUNTRIES"], "mood": "happy"}
        assert "TABLE main.countries" in user, "columns of the requested table were sent"
        return {"reply": "Here you go.", "sql": "SELECT country_code FROM countries", "tables_used": ["countries"],
                "mood": "happy"}
    if "read-only" in s and "<schema>" in s and "repair probe" in au:  # tests: wrong column, fixed after feedback
        if "failed validation" not in u:
            return {"reply": "Draft.", "sql": "SELECT o.ordr_dt FROM orders o", "mood": "happy"}
        assert "ORDER_DATE" in user, "real column list was sent"
        return {"reply": "Fixed.", "sql": "SELECT o.order_date FROM orders o", "mood": "happy"}
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
    all_user = "\n".join(m["content"] for m in msgs if m["role"] == "user")
    content = json.dumps(_answer(system, user, all_user)) if body.get("response_format") or "json" in system.lower() else "pong"
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
