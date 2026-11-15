"""The SSE snapshot streams must not hold a request-scoped DB session.

FastAPI keeps a `yield` dependency's value alive until the response is
finished, and a StreamingResponse is not finished until the stream ends. So a
stream that takes `Depends(get_db)` -- directly, or transitively through
`Depends(get_current_user)` -- pins one of the engine's connection pool slots
for as long as a client stays connected. With the pool at 25 per worker,
enough concurrent viewers exhaust it; and because the routers are sync `def`,
the requests that then block on checkout also occupy anyio threadpool threads,
which stops the API answering routes that touch no database at all.

The snapshot generators already open and close their own session per poll, so
these routes need a session only for the pre-flight ownership check. They do
that in a short-lived session via `_stream_owned_id`, and these tests pin that
arrangement so it cannot quietly regress.
"""

from fastapi.routing import APIRoute

from app.db import get_db
from app.main import app

SNAPSHOT_STREAMS = {
    "/v1/resumes/{resume_id}/import/stream",
    "/v1/resumes/{resume_id}/tailor-run/stream",
}

# The chat turn is a stream too, and was the last route holding a session for
# its whole life. It writes through the database *during* the stream, so it
# could not simply close early: every write now opens its own short-lived
# session and re-loads what it needs by id, the way `Meter` already did.
STREAMING_ROUTES = SNAPSHOT_STREAMS | {"/v1/resumes/{resume_id}/chat"}

# Not a stream, and so not in the set above: it returns one JSON body and the
# session may live as long as the response. It is asserted below as the
# control that keeps these tests honest -- if the dependency walk ever stops
# finding `get_db` anywhere, this is what fails.
PLAIN_JSON_ROUTE = "/v1/resumes/{resume_id}/messages"


def _transitive_calls(dependant):
    """Every callable in a route's dependency graph, including nested ones."""
    yield dependant.call
    for sub in dependant.dependencies:
        yield from _transitive_calls(sub)


def _walk(routes):
    """Every APIRoute in the app, descending into included routers.

    FastAPI 0.141 wraps `include_router` results in `_IncludedRouter` rather
    than flattening them onto `app.routes`, so a plain scan of `app.routes`
    finds only the four built-in doc routes and would make these tests pass by
    finding nothing.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _walk(included.routes)


def _route(path: str) -> APIRoute:
    for route in _walk(app.routes):
        if route.path == path:
            return route
    raise AssertionError(f"route {path} is not registered")


def test_streaming_routes_are_registered():
    # Guards the tests below against silently passing on a renamed route.
    for path in STREAMING_ROUTES | {PLAIN_JSON_ROUTE}:
        assert _route(path) is not None


def test_streaming_routes_do_not_depend_on_get_db():
    for path in STREAMING_ROUTES:
        calls = set(_transitive_calls(_route(path).dependant))
        assert get_db not in calls, (
            f"{path} takes get_db (perhaps via get_current_user), so it holds a "
            "pool connection for the whole life of the stream"
        )


def test_a_plain_json_route_still_depends_on_get_db():
    """The control. A non-streaming route may hold its session for the response.

    Without this, a dependency walk that silently stopped resolving anything
    would make the assertions above pass for the wrong reason.
    """
    calls = set(_transitive_calls(_route(PLAIN_JSON_ROUTE).dependant))
    assert get_db in calls
