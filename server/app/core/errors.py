"""RFC 7807 problem+json errors with catalogue codes (TS Appendix D)."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

CATALOGUE: dict[str, tuple[int, str]] = {
    "E-AUTH-001": (401, "Your session has expired — please sign in again."),
    "E-AUTH-002": (403, "You need administrator rights for this action."),
    "E-AUTH-003": (403, "You don't have access to this database."),
    "E-AUTH-004": (401, "Invalid user ID or password."),
    "E-AUTH-005": (423, "Too many failed attempts — try again in {n} minutes."),
    "E-AUTH-006": (422, "Password doesn't meet the policy: {rule}."),
    "E-AUTH-007": (503, "Directory service unavailable — try again shortly."),
    "E-AUTH-008": (403, "Invalid or missing CSRF token."),
    "E-RAG-001": (422, "Unsupported file type. Please upload a PDF, DOCX, Markdown (.md) or TXT file."),
    "E-RAG-002": (422, "File is larger than 50 MB. Please split or compress it and try again."),
    "E-RAG-003": (422, "The file looks corrupted or doesn't match its extension."),
    "E-RAG-004": (409, "This document is already uploaded in the same scope."),
    "E-RAG-005": (500, "We couldn't extract text from this file."),
    "E-CON-001": (502, "Couldn't connect to the database: {detail}."),
    "E-CON-002": (422, "Wallet/TNS configuration invalid."),
    "E-CON-003": (409, "The DuckDB file is locked by another process."),
    "E-CON-004": (422, "That file path isn't allowed."),
    "E-SCH-001": (409, "Resolve all tables pending review before locking."),
    "E-SCH-002": (409, "A crawl is already running for this connection."),
    "E-SCH-003": (404, "No locked schema snapshot for this connection yet."),
    "E-EXE-001": (422, "Only a single read-only SELECT statement is allowed."),
    "E-EXE-002": (422, "Syntax error at line {l}, column {c}: {msg}."),
    "E-EXE-003": (422, "Function {fn} is not allowed."),
    "E-EXE-010": (409, "Please review the query and click Execute."),
    "E-EXE-020": (409, "Large result intercepted — confirmation required."),
    "E-EXE-030": (504, "Query exceeded the time limit of {n} s."),
    "E-EXE-040": (410, "This result has expired — re-run the query."),
    "E-EXE-050": (502, "The database returned an error: {detail}."),
    "E-EXP-001": (500, "Export failed."),
    "E-EXP-002": (422, "Too many rows for Excel (max 1,048,575). Please filter the query."),
    "E-LLM-001": (503, "The AI service is unavailable — try again shortly."),
    "E-LLM-002": (502, "The AI returned an unexpected response."),
    "E-LLM-003": (422, "That message is too long for the selected model — try shortening it."),
    "E-LLM-004": (502, "AI endpoint TLS/proxy error: {detail}."),
    "E-PRG-001": (409, "A purge is already in progress."),
    "E-RATE-001": (429, "You're going a bit fast — try again in {n} s."),
    "E-SYS-001": (503, "The server is starting up or shutting down."),
    "E-NF-001": (404, "Not found."),
    "E-VAL-001": (422, "{detail}"),
}


class ApiError(Exception):
    def __init__(self, code: str, status: int | None = None, detail: str | None = None, **extra: Any):
        default_status, template = CATALOGUE.get(code, (status or 400, detail or code))
        self.code = code
        self.status = status or default_status
        fmt = dict(extra)
        if detail is not None:
            fmt["detail"] = detail
        try:
            self.message = template.format(**fmt) if fmt else template
        except (KeyError, IndexError):
            self.message = template.replace("{detail}", detail or "").replace(": .", ".")
        if detail and code not in CATALOGUE:
            self.message = detail
        self.extra = extra
        super().__init__(f"{code}: {self.message}")

    def body(self) -> dict[str, Any]:
        out = {"type": "about:blank", "code": self.code, "title": self.message, "status": self.status}
        out.update({k: v for k, v in self.extra.items() if k not in ("n", "rule", "l", "c", "msg", "fn")})
        return out


def not_found() -> ApiError:
    return ApiError("E-NF-001")


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    headers = {}
    if exc.status == 429 and "n" in exc.extra:
        headers["Retry-After"] = str(exc.extra["n"])
    return JSONResponse(exc.body(), status_code=exc.status, media_type="application/problem+json", headers=headers)
