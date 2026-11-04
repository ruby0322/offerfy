"""Server-sent-event framing shared by every SSE route.

Extracted from `app.routers.chat` (`SSE_HEADERS` / `_sse`) so a second route
outside that module -- the resume-import stream built in a later task -- can
reuse the exact same headers and frame format without importing a private
name from another router, which is the wrong direction. The bodies are
unchanged: this is a move, not a rewrite. See `app.services.chat_messages`
for the precedent this follows.
"""

from __future__ import annotations

import json

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_frame(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
