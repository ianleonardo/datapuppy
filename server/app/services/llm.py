"""LLM providers: Gemini and Spirare (OpenAI-compatible) via httpx (TS §11.1–11.5, DD-06, DD-15)."""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.config import Config
from ..core.crypto import keyring
from ..core.errors import ApiError
from ..store.duck import AppStore
from .settings import settings

log = logging.getLogger("sqlassist.llm")
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0


class ContextLengthError(Exception):
    pass


def heuristic_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 3)


class BaseProvider:
    kind = ""

    def __init__(self, row: dict[str, Any]):
        self.row = row
        self.params = row["params"] if isinstance(row["params"], dict) else json.loads(row["params"] or "{}")
        self.max_model_len = int(row.get("max_model_len") or 32768)
        self.max_output_tokens = int(row.get("max_output_tokens") or 4096)
        verify: Any = bool(row["verify_ssl"])
        if verify and row.get("ca_bundle_path"):
            verify = row["ca_bundle_path"]
        self.client = httpx.AsyncClient(
            verify=verify,                                   # False → TLS verification bypassed (DD-15)
            trust_env=bool(row["use_proxy"]),               # False → ignore HTTP(S)_PROXY / system proxy
            proxy=row.get("proxy_url") if row["use_proxy"] and row.get("proxy_url") else None,
            timeout=httpx.Timeout(int(row.get("timeout_s") or 120), connect=10),
        )
        self._tokenizer = None
        tp = self.params.get("tokenizer_path")
        if tp:
            try:
                from tokenizers import Tokenizer
                self._tokenizer = Tokenizer.from_file(tp)
            except Exception:  # noqa: BLE001
                log.warning("tokenizer at %s could not be loaded; using heuristic", tp)

    def api_key(self) -> str | None:
        r = self.row
        if not r.get("api_key_ciphertext"):
            return None
        return keyring().open_str(bytes(r["api_key_ciphertext"]), bytes(r["api_key_nonce"]), int(r["api_key_version"]),
                                  f"llm_providers:{r['id']}:api_key")

    def estimate_tokens(self, text: str) -> int:
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(text).ids)
        return heuristic_tokens(text)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _post(self, url: str, payload: dict, headers: dict) -> dict:
        try:
            resp = await self.client.post(url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ProxyError) as e:
            msg = str(e)
            host = httpx.URL(url).host
            log.warning("LLM %s unreachable at %s: %s: %s", self.kind, host, type(e).__name__, msg[:200] or "(no detail)")
            if "CERTIFICATE" in msg.upper() or "SSL" in msg.upper() or isinstance(e, httpx.ProxyError):
                raise ApiError("E-LLM-004", detail=msg[:200]) from e
            raise ApiError("E-LLM-001") from e
        except httpx.TimeoutException as e:
            log.warning("LLM %s timed out at %s (%s)", self.kind, httpx.URL(url).host, type(e).__name__)
            raise ApiError("E-LLM-001", retryable=True) from e
        if resp.status_code >= 400:
            body = resp.text[:500]
            if resp.status_code in (400, 413) and re.search(r"context|maximum.*length|too long|token", body, re.I):
                raise ContextLengthError(body)
            log.error("LLM HTTP %s from %s: %s", resp.status_code, self.kind, body[:200])
            raise ApiError("E-LLM-001", retryable=resp.status_code in (429, 500, 502, 503, 504),
                           upstream_status=resp.status_code)
        return resp.json()

    async def test(self) -> dict[str, Any]:
        out: dict[str, Any] = {"chat": None, "embed": None, "embed_dim": None}
        t0 = time.monotonic()
        try:
            r = await self.chat([{"role": "user", "content": "Reply with the single word: pong"}], max_tokens=16,
                                temperature=0, json_schema=None)
            out["chat"] = {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000), "sample": r.text[:40]}
        except Exception as e:  # noqa: BLE001
            out["chat"] = {"ok": False, "error": getattr(e, "message", str(e))[:200]}
        if self.row.get("embed_url") or self.kind == "GEMINI":
            t1 = time.monotonic()
            try:
                v = await self.embed(["dimension probe"])
                out["embed"] = {"ok": True, "latency_ms": int((time.monotonic() - t1) * 1000)}
                out["embed_dim"] = len(v[0])
            except Exception as e:  # noqa: BLE001
                out["embed"] = {"ok": False, "error": getattr(e, "message", str(e))[:200]}
        return out


class SpirareProvider(BaseProvider):
    """OpenAI-compatible gateway (TS §11.3)."""
    kind = "SPIRARE"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        h.update(self.params.get("extra_headers") or {})
        key = self.api_key()
        if key:
            name = self.params.get("auth_header", "Authorization")
            h[name] = f"Bearer {key}" if name.lower() == "authorization" else key
        return h

    async def chat(self, messages: list[dict], *, json_schema: dict | None = None, temperature: float | None = None,
                   max_tokens: int | None = None) -> LLMResult:
        payload: dict[str, Any] = {"model": self.row["chat_model"], "messages": messages,
                                   "temperature": self.params.get("temperature", 0.1) if temperature is None else temperature,
                                   "max_tokens": max_tokens or self.max_output_tokens, "stream": False}
        if self.params.get("top_p") is not None:
            payload["top_p"] = self.params["top_p"]
        mode = self.params.get("structured_output_mode", "json_schema")
        if json_schema is not None:
            if mode == "json_schema":
                payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": json_schema}}
            elif mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
        t0 = time.monotonic()
        data = await self._post(self.row["chat_url"], payload, self._headers())
        try:
            msg = data["choices"][0]["message"]
            text = _THINK.sub("", msg.get("content") or "").strip()  # reasoning_content is never used
        except (KeyError, IndexError, TypeError) as e:
            raise ApiError("E-LLM-002") from e
        u = data.get("usage") or {}
        return LLMResult(text, self.kind, self.row["chat_model"],
                         {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)},
                         int((time.monotonic() - t0) * 1000))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.row.get("embed_url"):
            raise ApiError("E-LLM-001")
        data = await self._post(self.row["embed_url"], {"model": self.row["embed_model"], "input": texts}, self._headers())
        try:
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            return [list(map(float, d["embedding"])) for d in items]
        except (KeyError, TypeError) as e:
            raise ApiError("E-LLM-002") from e


class GeminiProvider(BaseProvider):
    """Google Gemini REST (TS §11.2)."""
    kind = "GEMINI"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = self.api_key()
        if key:
            h["x-goog-api-key"] = key
        return h

    def _url(self, template: str, model: str) -> str:
        return template.replace("{model}", model.removeprefix("models/"))

    async def chat(self, messages: list[dict], *, json_schema: dict | None = None, temperature: float | None = None,
                   max_tokens: int | None = None) -> LLMResult:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] != "system"]
        gen: dict[str, Any] = {"temperature": self.params.get("temperature", 0.1) if temperature is None else temperature,
                               "maxOutputTokens": max_tokens or self.max_output_tokens}
        if json_schema is not None:
            gen["responseMimeType"] = "application/json"
            gen["responseSchema"] = _gemini_schema(json_schema)
        payload: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        t0 = time.monotonic()
        data = await self._post(self._url(self.row["chat_url"], self.row["chat_model"]), payload, self._headers())
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, TypeError) as e:
            raise ApiError("E-LLM-002") from e
        u = data.get("usageMetadata") or {}
        return LLMResult(text.strip(), self.kind, self.row["chat_model"],
                         {"input": u.get("promptTokenCount", 0), "output": u.get("candidatesTokenCount", 0)},
                         int((time.monotonic() - t0) * 1000))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.row.get("embed_model") or "gemini-embedding-2"
        url = self._url(self.row.get("embed_url") or "", model)
        reqs = []
        for t in texts:
            r: dict[str, Any] = {"model": f"models/{model.removeprefix('models/')}", "content": {"parts": [{"text": t}]}}
            if self.params.get("output_dimensionality", 1024):
                r["outputDimensionality"] = int(self.params.get("output_dimensionality", 1024))
            reqs.append(r)
        data = await self._post(url, {"requests": reqs}, self._headers())
        try:
            return [list(map(float, e["values"])) for e in data["embeddings"]]
        except (KeyError, TypeError) as e:
            raise ApiError("E-LLM-002") from e


def _gemini_schema(schema: dict) -> dict:
    """Gemini responseSchema supports an OpenAPI subset: drop unsupported keywords."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in ("additionalProperties", "$schema", "title", "default"):
            continue
        if k == "properties":
            out[k] = {pk: _gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _gemini_schema(v)
        elif k == "type" and isinstance(v, list):
            out[k] = next((t for t in v if t != "null"), "string")
            out["nullable"] = "null" in v
        else:
            out[k] = v
    return out


PROVIDERS = {"SPIRARE": SpirareProvider, "GEMINI": GeminiProvider}


# ----------------------------------------------------------------------------- routing
class Router:
    """Primary chat + primary embedding providers, chat failover on timeout/5xx/429 (TS §11.5)."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, BaseProvider]] = {}

    def _rows(self, store: AppStore) -> list[dict]:
        return store.fetchall_sync("SELECT * FROM llm_providers WHERE is_enabled ORDER BY is_primary_chat DESC, kind")

    def _get(self, row: dict) -> BaseProvider:
        sig = json.dumps({k: v for k, v in row.items() if k != "last_test"}, default=str)
        cached = self._cache.get(str(row["id"]))
        if cached and cached[0] == sig:
            return cached[1]
        p = PROVIDERS[row["kind"]](row)
        self._cache[str(row["id"])] = (sig, p)
        return p

    def invalidate(self) -> None:
        self._cache.clear()  # config changes take effect on next request, no restart

    def provider_by_id(self, store: AppStore, pid: str) -> BaseProvider:
        rows_ = store.fetchall_sync("SELECT * FROM llm_providers WHERE id = ?", [pid])
        if not rows_:
            raise ApiError("E-NF-001")
        return PROVIDERS[rows_[0]["kind"]](rows_[0])

    def chat_candidates(self, store: AppStore) -> list[BaseProvider]:
        rows_ = self._rows(store)
        primary = [r for r in rows_ if r["is_primary_chat"]]
        others = [r for r in rows_ if not r["is_primary_chat"]]
        chain = primary[:1] + (others if settings.get("ai.failover_enabled", True) else [])
        if not primary:
            chain = rows_[:1]
        return [self._get(r) for r in chain]

    def embedder(self, store: AppStore) -> BaseProvider | None:
        """Embeddings never fail over — vectors are model-specific (TS §8.3)."""
        rows_ = [r for r in self._rows(store) if r["is_primary_embed"]]
        return self._get(rows_[0]) if rows_ else None

    async def chat(self, store: AppStore, messages: list[dict], *, json_schema: dict | None = None,
                   max_tokens: int | None = None, intent: str = "") -> LLMResult:
        cands = self.chat_candidates(store)
        if not cands:
            raise ApiError("E-LLM-001")
        last: Exception | None = None
        for i, p in enumerate(cands):
            try:
                res = await p.chat(messages, json_schema=json_schema, max_tokens=max_tokens)
                _llm_log(p, intent, res, "OK")
                return res
            except ApiError as e:
                last = e
                _llm_log(p, intent, None, e.code)
                if not e.extra.get("retryable") and e.code != "E-LLM-001":
                    raise
                if i + 1 < len(cands):
                    log.warning("chat failover from %s after %s", p.kind, e.code)
        raise last or ApiError("E-LLM-001")


def _llm_log(p: BaseProvider, intent: str, res: LLMResult | None, status: str) -> None:
    lg = logging.getLogger("sqlassist.llm")
    extra = {"provider": p.kind, "model": p.row["chat_model"], "intent": intent, "status": status}
    if res:
        extra.update({"tokens": res.usage, "latency_ms": res.latency_ms})
    lg.info("llm call", extra={"extra": extra})


router = Router()


# ----------------------------------------------------------------------------- budgeting (TS §11.4)
def input_budget(p: BaseProvider) -> int:
    cap = int(settings.get("ai.prompt_budget_cap", 64000))
    return int((min(p.max_model_len, cap) - p.max_output_tokens) * 0.95)


def fit(p: BaseProvider, fixed: str, segments: list[tuple[str, list[str], float]], user_prompt: str,
        shrink: float = 1.0) -> dict[str, list[str]]:
    """Fill segments in order with share-of-remaining caps; items are pre-sorted best-first (lowest trimmed first)."""
    budget = input_budget(p)
    used = p.estimate_tokens(fixed) + p.estimate_tokens(user_prompt)
    if p.estimate_tokens(user_prompt) > budget * 0.8:
        raise ApiError("E-LLM-003")
    out: dict[str, list[str]] = {}
    for name, items, share in segments:
        remaining = max(0, budget - used)
        cap = int(remaining * share * (shrink if name in ("knowledge", "history") else 1.0))
        taken, spent = [], 0
        for it in items:
            t = p.estimate_tokens(it)
            if spent + t > cap:
                break
            taken.append(it)
            spent += t
        out[name] = taken
        used += spent
    return out


# ----------------------------------------------------------------------------- seeding
def seed_providers(store: AppStore, cfg: Config) -> None:
    if store.fetchall_sync("SELECT count(*) AS n FROM llm_providers")[0]["n"]:
        _import_toml_keys(store, cfg)
        return
    g, s = cfg.get("llm.gemini"), cfg.get("llm.spirare")
    prim_chat = cfg.get("llm.defaults.primary_chat", "SPIRARE")
    prim_embed = cfg.get("llm.defaults.primary_embed", "SPIRARE")
    base = g["base_url"].rstrip("/")

    def unit(c):
        c.execute("INSERT INTO llm_providers(kind, display_name, chat_url, chat_model, embed_url, embed_model, embed_dim, "
                  "max_model_len, max_output_tokens, verify_ssl, use_proxy, timeout_s, params, is_primary_chat, is_primary_embed) "
                  "VALUES ('SPIRARE', 'Spirare (internal)', ?, ?, ?, ?, 1024, ?, ?, ?, ?, 120, ?, ?, ?)",
                  [s["chat_url"], s["chat_model"], s["embed_url"], s["embed_model"], s["max_model_len"],
                   s.get("max_output_tokens", 4096), s["verify_ssl"], s["use_proxy"],
                   json.dumps({"temperature": 0.1, "structured_output_mode": "json_schema", "auth_header": "Authorization",
                               "embed_batch": 32}), prim_chat == "SPIRARE", prim_embed == "SPIRARE"])
        c.execute("INSERT INTO llm_providers(kind, display_name, chat_url, chat_model, embed_url, embed_model, embed_dim, "
                  "max_model_len, max_output_tokens, verify_ssl, use_proxy, timeout_s, params, is_primary_chat, is_primary_embed) "
                  "VALUES ('GEMINI', 'Google Gemini', ?, ?, ?, ?, 1024, ?, ?, ?, ?, 120, ?, ?, ?)",
                  [base + "/models/{model}:generateContent", g["chat_model"], base + "/models/{model}:batchEmbedContents",
                   g["embed_model"], g.get("max_model_len", 1_000_000), g.get("max_output_tokens", 8192), g["verify_ssl"],
                   g["use_proxy"], json.dumps({"temperature": 0.1, "output_dimensionality": 1024, "embed_batch": 64}),
                   prim_chat == "GEMINI", prim_embed == "GEMINI"])
    store.write_sync(unit)
    _import_toml_keys(store, cfg)


def _import_toml_keys(store: AppStore, cfg: Config) -> None:
    """API keys put in the TOML are imported once into the encrypted store (TS §24.2)."""
    for kind, section in (("SPIRARE", "llm.spirare"), ("GEMINI", "llm.gemini")):
        key = cfg.get(f"{section}.api_key")
        if not key:
            continue
        row = store.fetchall_sync("SELECT id, api_key_ciphertext FROM llm_providers WHERE kind = ? LIMIT 1", [kind])
        if row and not row[0]["api_key_ciphertext"]:
            pid = str(row[0]["id"])
            sealed = keyring().seal(key, f"llm_providers:{pid}:api_key")
            store.write_sync(lambda c, pid=pid, sealed=sealed: c.execute(
                "UPDATE llm_providers SET api_key_ciphertext = ?, api_key_nonce = ?, api_key_version = ? WHERE id = ?",
                [sealed.ciphertext, sealed.nonce, sealed.key_version, pid]))
        log.warning("API key for %s found in config file and imported into the encrypted store — remove it from %s",
                    kind, cfg.config_file)


def parse_json_reply(text: str) -> dict[str, Any]:
    """prompt_only mode: JSON may be fenced or surrounded by prose."""
    t = _THINK.sub("", text).strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            t = t[start:end + 1]
    return json.loads(t)
