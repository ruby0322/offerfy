"""MCP protocol eras: which one a request speaks, and whether this server
accepts it. Both eras are served from the one endpoint and the one tool
registry; this module owns only version validation and `server/discover`.

Read on 2026-11-20 from the published specification:
  https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
  https://modelcontextprotocol.io/specification/2026-07-28/basic/lifecycle
  https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
  https://modelcontextprotocol.io/specification/2026-07-28/server/discover
  https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching

Why both eras exist here. The current revision, 2026-07-28, is *modern*: no
`initialize`, the version travels as per-request `_meta`, and `server/discover`
("Servers MUST implement it") replaces the handshake. Revisions through
2025-11-25 are *legacy* (an `initialize` handshake). The backward-compatibility
matrix says of a modern client against a legacy-only server: "Fails. The
server may reject the request with an implementation-defined error, stay
silent, or even process an era-ambiguous method under legacy semantics." And a
dual-era client against a legacy-only server "Works ... the modern request
returns a 4xx without a recognized modern error body, and the client falls
back to `initialize`", but that probe is a MAY and the body inspection a
SHOULD, i.e. other people's optional code. A feature whose purpose is "connect
your assistant" cannot rest on that, so both eras are served.

The -32022 error below is load-bearing for the same reason: a dual-era client
inspects a 400 body and, "if the body contains a recognized modern JSON-RPC
error", stays modern and retries with a version from `data.supported` rather
than falling back. A malformed one would push it into the legacy path.

Per the spec, an `initialize` sent with a modern version is answered with an
error that names `supported`, and `server/discover` under a legacy version is
method-not-found: each method belongs to exactly one era.
"""

from typing import Any

from app.services import mcp_tools

MODERN_VERSION = "2026-07-28"
LEGACY_VERSION = mcp_tools.PROTOCOL_VERSION  # "2025-11-25"
SUPPORTED = (MODERN_VERSION, LEGACY_VERSION)

META_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"

HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022
METHOD_NOT_FOUND = -32601

# The tool list is static per deploy (a Python dict in app/services/mcp_tools),
# so a long TTL is honest: the only thing that changes it is a release, and
# TTL is a freshness hint, not a guarantee. One hour bounds how long a client
# can keep a stale list after a deploy.
DISCOVER_TTL_MS = 3_600_000
# The answer is identical for every caller (it names no per-user data and no
# credential-dependent tool), so a shared cache may hold it.
CACHE_SCOPE = "public"

INSTRUCTIONS = (
    "Offerfy's tools act on the signed-in user's own resumes. Call tools/list "
    "to see them."
)


class ProtocolError(Exception):
    """A version or header problem. The router renders it as a JSON-RPC error
    with this HTTP status, using the request's own id."""

    def __init__(self, status: int, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.data = data


def _meta_version(body: Any) -> str | None:
    params = body.get("params") if isinstance(body, dict) else None
    meta = params.get("_meta") if isinstance(params, dict) else None
    value = meta.get(META_VERSION_KEY) if isinstance(meta, dict) else None
    return value if isinstance(value, str) else None


def _header_version(request) -> str | None:
    return request.headers.get("mcp-protocol-version")


def era_of(request, body) -> str:
    """"modern" or "legacy". Any version evidence other than the legacy
    revision means modern (an unsupported one too, so it earns the modern
    -32022 rather than a legacy answer). With no evidence at all the request
    is legacy, which covers `initialize` and every client that never sends the
    header; the one exception is `server/discover`, which exists only in the
    modern era and is therefore judged as a (malformed) modern request."""
    seen = [v for v in (_header_version(request), _meta_version(body)) if v is not None]
    if any(v != LEGACY_VERSION for v in seen):
        return "modern"
    if seen:
        return "legacy"
    method = body.get("method") if isinstance(body, dict) else None
    return "modern" if method == "server/discover" else "legacy"


def _mismatch(detail: str) -> ProtocolError:
    # `supported` rides along (the spec does not ask for it here) so a client
    # that probes with a header/body disagreement still learns what to send.
    return ProtocolError(
        400, HEADER_MISMATCH, f"HeaderMismatch: {detail}", {"supported": list(SUPPORTED)}
    )


def check_version(request, body) -> None:
    """Raise ProtocolError when the request's version claims are unacceptable."""
    header = _header_version(request)
    meta = _meta_version(body)

    # Mcp-Method is REQUIRED for compliance, but a missing one is accepted on
    # purpose: clients in the wild omit it, and the method is read from the
    # body either way. Only a header that is present and disagrees is
    # rejected, because a router trusting the header would then disagree with
    # the code that executes the body.
    method_header = request.headers.get("mcp-method")
    if method_header is not None and isinstance(body, dict) and method_header != body.get("method"):
        raise _mismatch("Mcp-Method does not match the request method")

    if header is not None and meta is not None and header != meta:
        raise _mismatch("MCP-Protocol-Version does not match _meta protocolVersion")

    if era_of(request, body) == "legacy":
        return
    requested = header if header is not None else meta
    if requested not in SUPPORTED:
        raise ProtocolError(
            400,
            UNSUPPORTED_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED), "requested": (requested or "")[:32]},
        )
    if header is None:
        raise _mismatch("MCP-Protocol-Version header is required")
    if meta is None:
        raise _mismatch("_meta protocolVersion is required")


def discover_result() -> dict:
    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED),
        "capabilities": {"tools": {"listChanged": False}},
        "_meta": {SERVER_INFO_KEY: mcp_tools.SERVER_INFO},
        "instructions": INSTRUCTIONS,
        "ttlMs": DISCOVER_TTL_MS,
        "cacheScope": CACHE_SCOPE,
    }
