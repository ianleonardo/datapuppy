"""Chat sessions & SSE messages (TS §20.2, §15)."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...auth.deps import current_ctx
from ...auth.ratelimit import limit
from ...core.errors import ApiError
from ...services.execution import load_connection
from ...services.orchestrator import answer, p
from ...services.settings import settings
from ...store.duck import store
from ...store.repos import ChatRepo, RequestContext

router = APIRouter(tags=["chat"])
log = logging.getLogger("sqlassist.chat")
_inflight: dict[str, asyncio.Task] = {}


class SessionIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    connection_id: str | None = None


class MessageIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=60_000)
    intent: str | None = None
    run_id: str | None = None
    content: list[dict] | None = None  # BlockNote JSON from the prompt editor


class FeedbackIn(BaseModel):
    value: int = Field(ge=-1, le=1)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/chat/sessions")
async def list_sessions(q: str | None = None, cursor: str | None = None, ctx: RequestContext = Depends(current_ctx)):
    items = await ChatRepo(store(), ctx).list_sessions(q, 50, cursor)
    return {"items": items, "next_cursor": str(items[-1]["updated_at"]) if len(items) == 50 else None}


@router.post("/chat/sessions")
async def create_session(body: SessionIn, ctx: RequestContext = Depends(current_ctx)):
    if body.connection_id:
        await load_connection(store(), ctx, body.connection_id)
    return await ChatRepo(store(), ctx).create_session(body.connection_id, body.title or "New chat")


@router.get("/chat/sessions/{sid}")
async def get_session(sid: str, after_seq: int = 0, ctx: RequestContext = Depends(current_ctx)):
    repo = ChatRepo(store(), ctx)
    sess = await repo.get_session(sid)
    return {**sess, "messages": await repo.messages(sid, 200, after_seq)}


@router.patch("/chat/sessions/{sid}")
async def patch_session(sid: str, body: SessionIn, ctx: RequestContext = Depends(current_ctx)):
    repo = ChatRepo(store(), ctx)
    sess = await repo.get_session(sid)
    if body.connection_id and str(sess["connection_id"]) != body.connection_id:
        conn = await load_connection(store(), ctx, body.connection_id)
        await repo.update_session(sid, connection_id=body.connection_id)
        await repo.add_message(sid, "SYSTEM_EVENT", [p(f"Switched to {conn['name']} ({conn['engine']}, {conn['country_code']})")],
                               f"Switched connection to {conn['name']}", {"event": "connection_switch",
                                                                          "connection_id": body.connection_id})
    if body.title:
        await repo.update_session(sid, title=body.title)
    return await repo.get_session(sid)


@router.delete("/chat/sessions/{sid}")
async def delete_session(sid: str, ctx: RequestContext = Depends(current_ctx)):
    await ChatRepo(store(), ctx).delete_session(sid)
    return {"ok": True}


@router.post("/chat/messages/{mid}/feedback")
async def feedback(mid: str, body: FeedbackIn, ctx: RequestContext = Depends(current_ctx)):
    await ChatRepo(store(), ctx).set_feedback(mid, body.value)
    return {"ok": True}


@router.post("/chat/messages/{mid}/cancel")
async def cancel(mid: str, ctx: RequestContext = Depends(current_ctx)):
    t = _inflight.get(f"{ctx.user_ref}:{mid}")
    if t:
        t.cancel()
    return {"ok": bool(t)}


@router.post("/chat/sessions/{sid}/messages")
async def post_message(sid: str, body: MessageIn, request: Request, ctx: RequestContext = Depends(current_ctx)):
    limit("chat", ctx.user_ref, per_min=settings.get("ratelimit.chat_per_min", 30))
    s = store()
    repo = ChatRepo(s, ctx)
    sess = await repo.get_session(sid)
    content = body.content or [p(body.prompt)]
    first = await repo.message_count(sid) == 0
    user_msg = await repo.add_message(sid, "USER", content, body.prompt, {"intent": body.intent} if body.intent else {})
    if first or sess["title"] == "New chat":
        await repo.update_session(sid, title=body.prompt.strip().splitlines()[0][:60])
    assistant_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, data: dict) -> None:
        await queue.put((event, data))

    async def work() -> None:
        try:
            res = await answer(s, ctx, sid, body.prompt, body.intent, body.run_id, emit)
            await emit("mood", {"mood": "thinking"})
            text = res["plain"] if not res["blocks"] or res["blocks"][0]["type"] != "paragraph" else \
                "".join(c.get("text", "") for c in res["blocks"][0]["content"])
            for i in range(0, len(text), 24):  # progressive reveal of the reply text
                await emit("token.delta", {"text": text[i:i + 24]})
                await asyncio.sleep(0.012)
            for b in res["blocks"][1:]:
                kind = {"sqlCard": "sql.proposed", "mermaid": "mermaid", "insight": "insight"}.get(b["type"], "block")
                await emit(kind, b)
            await repo.add_message(sid, "ASSISTANT", res["blocks"], res["plain"], res["artifacts"], res["llm_meta"],
                                   msg_id=assistant_id)
            await emit("mood", {"mood": res["mood"]})
            await emit("message.completed", {"id": assistant_id, "role": "ASSISTANT", "content": res["blocks"],
                                             "artifacts": res["artifacts"], "llm_meta": res["llm_meta"]})
        except asyncio.CancelledError:
            await repo.add_message(sid, "ASSISTANT", [p("(stopped)")], "(stopped)", {"incomplete": True, "mood": "idle"},
                                   msg_id=assistant_id)
            await emit("message.completed", {"id": assistant_id, "role": "ASSISTANT", "content": [p("(stopped)")],
                                             "artifacts": {"incomplete": True}})
        except ApiError as e:
            await emit("mood", {"mood": "confused"})
            await emit("error", {"code": e.code, "title": e.message})
        except Exception:  # noqa: BLE001
            log.exception("chat generation failed")
            await emit("mood", {"mood": "confused"})
            await emit("error", {"code": "E-LLM-002", "title": "Something went wrong while I was thinking."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    key = f"{ctx.user_ref}:{assistant_id}"
    _inflight[key] = task

    async def stream():
        try:
            yield sse("message.started", {"id": assistant_id, "user_message_id": user_msg["id"]})
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield sse(*item)
        finally:
            _inflight.pop(key, None)
            if await request.is_disconnected() and not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
