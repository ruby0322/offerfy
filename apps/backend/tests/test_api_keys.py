"""The credential pool: parsing, failover, and the cooldown.

Three layers, because each can be wrong on its own:

  * the pool itself -- what `OPENAI_API_KEY=a,b,c` means, and which key the
    next call gets;
  * `api_keys.post`, the single-request path the five non-streaming callers
    share (layout, resume_facts, resume_content, jobs.score, jobs.reasons);
  * the two chat adapters, driven through a real turn, because their failover
    has to unwind a streaming context manager and must never replay output.

The invariant worth stating once: **a pool of one behaves exactly like the
single key it replaced** -- one attempt, and the provider's own error surfaced
to the caller. Every test that configures one key is also a regression test
for every deployment that never sets a second.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import api_keys
from tests.openai_script import ScriptedStream, llm_message, sse_lines


@pytest.fixture
def pool(monkeypatch):
    """Set a provider's key list from a test, settings cache and all."""

    def configure(value: str, provider: str = api_keys.OPENAI) -> None:
        env = "OPENAI_API_KEY" if provider == api_keys.OPENAI else "ANTHROPIC_API_KEY"
        monkeypatch.setenv(env, value)
        get_settings.cache_clear()
        api_keys.reset()

    return configure


def expire(credential: api_keys.Credential) -> None:
    """Pretend this key's cooldown has run out.

    Reaches into the module's table on purpose. The alternative is patching
    `time.monotonic`, which the event loop also uses, or sleeping out a real
    cooldown measured in minutes.
    """
    api_keys._cooling[credential.fingerprint] = time.monotonic() - 1.0


# --------------------------------------------------------------------------
# What a key list means
# --------------------------------------------------------------------------


def test_one_key_is_a_pool_of_one(pool):
    pool("sk-only")
    assert api_keys.keys(api_keys.OPENAI) == ("sk-only",)
    assert api_keys.configured(api_keys.OPENAI) is True
    assert api_keys.count(api_keys.OPENAI) == 1


def test_no_key_is_an_empty_pool(pool):
    pool("")
    assert api_keys.keys(api_keys.OPENAI) == ()
    assert api_keys.configured(api_keys.OPENAI) is False
    assert api_keys.rotation(api_keys.OPENAI) == ()


def test_keys_split_on_commas_and_whitespace(pool):
    # Commas are documented; whitespace is tolerated because an env file
    # edited by hand acquires it, and a key with a stray space would
    # otherwise fail authentication for no visible reason.
    pool("sk-a, sk-b ,sk-c\tsk-d\nsk-e")
    assert api_keys.keys(api_keys.OPENAI) == ("sk-a", "sk-b", "sk-c", "sk-d", "sk-e")


def test_repeats_and_blanks_are_dropped(pool):
    # A list naming the same key twice is a paste, not a weighting -- and
    # keeping it would make a cooled-down key look healthy at its second
    # position.
    pool("sk-a,,sk-b,sk-a,  ,sk-b")
    assert api_keys.keys(api_keys.OPENAI) == ("sk-a", "sk-b")


def test_the_two_providers_have_separate_pools(pool):
    pool("sk-openai")
    pool("sk-ant-1,sk-ant-2", provider=api_keys.ANTHROPIC)
    assert api_keys.count(api_keys.OPENAI) == 1
    assert api_keys.count(api_keys.ANTHROPIC) == 2


def test_an_unknown_provider_has_no_keys(pool):
    pool("sk-a,sk-b")
    assert api_keys.keys("gemini") == ()
    assert api_keys.configured("gemini") is False


# --------------------------------------------------------------------------
# Which key the next call gets
# --------------------------------------------------------------------------


def test_rotation_keeps_the_configured_order(pool):
    """The first key wins while it is healthy -- this is failover, not
    round-robin, because both vendors cache prompt prefixes per key."""
    pool("sk-a,sk-b,sk-c")
    order = api_keys.rotation(api_keys.OPENAI)
    assert [credential.secret for credential in order] == ["sk-a", "sk-b", "sk-c"]
    assert [credential.slot for credential in order] == [1, 2, 3]
    assert {credential.total for credential in order} == {3}


def test_rotation_does_not_move_after_a_good_call(pool):
    pool("sk-a,sk-b")
    first = api_keys.rotation(api_keys.OPENAI)[0]
    assert api_keys.rotation(api_keys.OPENAI)[0].secret == first.secret


def test_a_penalised_key_goes_to_the_back(pool):
    pool("sk-a,sk-b,sk-c")
    first = api_keys.rotation(api_keys.OPENAI)[0]
    api_keys.penalize(first, status=429)
    order = api_keys.rotation(api_keys.OPENAI)
    assert [credential.secret for credential in order] == ["sk-b", "sk-c", "sk-a"]
    assert api_keys.cooling_down(api_keys.OPENAI) == 1


def test_an_exhausted_pool_still_offers_every_key(pool):
    """Cooling keys are returned, not withheld.

    An exhausted pool has to degrade into the provider's own error -- the one
    a single key would have produced -- and not into a 503 this module made
    up.
    """
    pool("sk-a,sk-b")
    first, second = api_keys.rotation(api_keys.OPENAI)
    api_keys.penalize(second, status=429, retry_after="5")
    api_keys.penalize(first, status=429, retry_after="30")
    order = api_keys.rotation(api_keys.OPENAI)
    # Soonest to recover first, so the next request tries the key that is
    # closest to being allowed again.
    assert [credential.secret for credential in order] == ["sk-b", "sk-a"]
    assert api_keys.cooling_down(api_keys.OPENAI) == 2


def test_a_cooldown_runs_out(pool):
    pool("sk-a,sk-b")
    first = api_keys.rotation(api_keys.OPENAI)[0]
    api_keys.penalize(first, status=429)
    assert api_keys.rotation(api_keys.OPENAI)[0].secret == "sk-b"
    expire(first)
    assert api_keys.rotation(api_keys.OPENAI)[0].secret == "sk-a"
    assert api_keys.cooling_down(api_keys.OPENAI) == 0


def test_retry_after_is_honoured(pool):
    pool("sk-a")
    credential = api_keys.rotation(api_keys.OPENAI)[0]
    assert api_keys.penalize(credential, status=429, retry_after="12") == 12.0


def test_retry_after_is_clamped(pool):
    """A proxy answering with a day's worth of seconds must not take a key out
    of the pool until the process restarts."""
    pool("sk-a")
    credential = api_keys.rotation(api_keys.OPENAI)[0]
    assert api_keys.penalize(credential, status=429, retry_after="86400") == (
        api_keys.MAX_COOLDOWN
    )


@pytest.mark.parametrize("value", ["", "   ", "0", "-5", "soon", "Wed, 21 Oct 2026 07:28:00 GMT"])
def test_an_unusable_retry_after_falls_back_to_the_default(pool, value):
    # The HTTP-date form is deliberately not parsed: the default is the right
    # order of magnitude anyway, and a date misread as a duration is how a key
    # leaves the pool for a week.
    pool("sk-a")
    credential = api_keys.rotation(api_keys.OPENAI)[0]
    applied = api_keys.penalize(credential, status=429, retry_after=value)
    assert applied == api_keys.COOLDOWN_THROTTLED


def test_a_rejected_key_cools_down_for_much_longer(pool):
    """401/403 is the old key of a half-finished rotation. Retrying it once a
    minute is pointless; the next key in the list is the new one."""
    pool("sk-old,sk-new")
    credential = api_keys.rotation(api_keys.OPENAI)[0]
    assert api_keys.penalize(credential, status=401) == api_keys.COOLDOWN_REJECTED
    assert api_keys.penalize(credential, status=403) == api_keys.COOLDOWN_REJECTED


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504, 529, 401, 403])
def test_these_statuses_move_to_the_next_key(status):
    assert api_keys.should_rotate(status) is True


@pytest.mark.parametrize("status", [200, 201, 400, 404, 409, 413, 422])
def test_these_statuses_do_not(status):
    """A bad request is about the request. Sending it again on another key
    would pay for the same rejection twice."""
    assert api_keys.should_rotate(status) is False


# --------------------------------------------------------------------------
# Naming a key without leaking it
# --------------------------------------------------------------------------


def test_a_fingerprint_does_not_contain_the_secret(pool):
    pool("sk-super-secret-value")
    credential = api_keys.rotation(api_keys.OPENAI)[0]
    assert "sk-super-secret-value" not in credential.fingerprint
    assert "sk-super-secret-value" not in str(credential)
    assert credential.fingerprint.startswith("openai:")


def test_the_same_string_in_both_providers_cools_down_separately(pool):
    """A misconfigured host sets one value as both keys. Throttling the
    OpenAI one must not make the Anthropic one look throttled."""
    pool("sk-same")
    pool("sk-same", provider=api_keys.ANTHROPIC)
    api_keys.penalize(api_keys.rotation(api_keys.OPENAI)[0], status=429)
    assert api_keys.cooling_down(api_keys.OPENAI) == 1
    assert api_keys.cooling_down(api_keys.ANTHROPIC) == 0


def test_each_provider_presents_its_key_its_own_way(pool):
    pool("sk-a")
    pool("sk-ant", provider=api_keys.ANTHROPIC)
    assert api_keys.rotation(api_keys.OPENAI)[0].header() == {
        "Authorization": "Bearer sk-a"
    }
    # `x-api-key`, not a bearer token: an Authorization header is only correct
    # for an OAuth access token on that API.
    assert api_keys.rotation(api_keys.ANTHROPIC)[0].header() == {"x-api-key": "sk-ant"}


def test_reset_forgets_every_cooldown(pool):
    pool("sk-a,sk-b")
    api_keys.penalize(api_keys.rotation(api_keys.OPENAI)[0], status=429)
    api_keys.reset()
    assert api_keys.cooling_down(api_keys.OPENAI) == 0
    assert api_keys.rotation(api_keys.OPENAI)[0].secret == "sk-a"


# --------------------------------------------------------------------------
# api_keys.post -- the single-request path
# --------------------------------------------------------------------------


class RecordingClient:
    """An httpx.AsyncClient double that answers each POST from a script."""

    def __init__(self, statuses: list[int], headers: dict | None = None):
        self.statuses = list(statuses)
        self.response_headers = headers or {}
        self.sent: list[dict] = []

    async def post(self, url, headers=None, json=None, **kwargs):
        self.sent.append({"url": url, "headers": dict(headers or {}), "body": json})
        status = self.statuses.pop(0) if self.statuses else 200
        return ScriptedStream(
            {"ok": status < 400}, status_code=status, headers=self.response_headers
        )

    def bearers(self) -> list[str]:
        return [sent["headers"].get("Authorization") for sent in self.sent]


def post(client, provider=api_keys.OPENAI, headers=None):
    return asyncio.run(
        api_keys.post(
            client,
            "https://example.test/v1/responses",
            provider=provider,
            headers=headers if headers is not None else {"Content-Type": "x"},
            json={"model": "m"},
        )
    )


def test_post_sends_one_request_when_the_first_key_works(pool):
    pool("sk-a,sk-b")
    client = RecordingClient([200])
    response = post(client)
    assert response.status_code == 200
    assert client.bearers() == ["Bearer sk-a"]
    # The caller's own headers survive alongside the credential.
    assert client.sent[0]["headers"]["Content-Type"] == "x"


def test_post_fails_over_to_the_next_key_on_a_429(pool):
    pool("sk-a,sk-b")
    client = RecordingClient([429, 200])
    response = post(client)
    assert response.status_code == 200
    assert client.bearers() == ["Bearer sk-a", "Bearer sk-b"]
    # And the throttled key is skipped by the request after this one.
    assert api_keys.rotation(api_keys.OPENAI)[0].secret == "sk-b"


@pytest.mark.parametrize("status", [500, 503, 529, 401])
def test_post_fails_over_on_every_rotating_status(pool, status):
    pool("sk-a,sk-b")
    client = RecordingClient([status, 200])
    assert post(client).status_code == 200
    assert client.bearers() == ["Bearer sk-a", "Bearer sk-b"]


def test_post_does_not_rotate_a_bad_request(pool):
    """A 400 is about the body. Paying another key to reject it is waste."""
    pool("sk-a,sk-b")
    client = RecordingClient([400, 200])
    assert post(client).status_code == 400
    assert client.bearers() == ["Bearer sk-a"]
    assert api_keys.cooling_down(api_keys.OPENAI) == 0


def test_post_returns_the_last_error_when_every_key_is_throttled(pool):
    pool("sk-a,sk-b,sk-c")
    client = RecordingClient([429, 429, 503])
    response = post(client)
    assert response.status_code == 503
    assert len(client.sent) == 3
    assert api_keys.cooling_down(api_keys.OPENAI) == 3


def test_post_with_one_key_tries_once_and_returns_its_error(pool):
    """The regression test for every deployment that never sets a second key:
    one attempt, the provider's own status, no extra latency."""
    pool("sk-only")
    client = RecordingClient([429])
    assert post(client).status_code == 429
    assert len(client.sent) == 1


def test_post_reads_retry_after_off_the_response(pool):
    pool("sk-a,sk-b")
    client = RecordingClient([429, 200], headers={"retry-after": "7"})
    post(client)
    # Expiring it proves the cooldown it set was the short one it was told.
    credential = next(
        credential
        for credential in api_keys.rotation(api_keys.OPENAI)
        if credential.secret == "sk-a"
    )
    assert api_keys._cooling[credential.fingerprint] - time.monotonic() <= 7.0


class ScriptedHeaders(RecordingClient):
    """`RecordingClient`, but each answer carries its own headers.

    The case under test needs two keys to fail differently in one chain: one
    with no `Retry-After` (so it cools for the default minute) and one that
    asks for a couple of seconds. A single `headers` for the whole script
    cannot say that, and it is the difference between a wait worth taking and
    one that is not.
    """

    def __init__(self, script: list[tuple[int, dict]]):
        super().__init__([status for status, _ in script])
        self.script_headers = [headers for _, headers in script]

    async def post(self, url, headers=None, json=None, **kwargs):
        self.response_headers = (
            self.script_headers.pop(0) if self.script_headers else {}
        )
        return await super().post(url, headers=headers, json=json, **kwargs)


@pytest.fixture
def slept(monkeypatch):
    """Record the in-request waits instead of taking them.

    `api_keys._sleep` exists as a named seam for exactly this. Patching
    `asyncio.sleep` itself would also slow the loop the test is running on.
    """
    waits: list[float] = []

    async def record(seconds):
        waits.append(seconds)

    monkeypatch.setattr(api_keys, "_sleep", record)
    return waits


def test_post_waits_once_for_a_key_that_is_about_to_come_back(pool, slept):
    # Failover has nothing left to try when every key in the pool is
    # throttled, and time is the only thing that can still help. Observed in
    # production 2026-11-27: one key out of credits (a 429 with no
    # `Retry-After`, so the default minute) and one rate limited for six
    # seconds, so every pair that reached the second key during its window was
    # simply lost.
    # The two keys are deliberately the other way round from production here:
    # the one asking for a SHORT wait is the one tried FIRST, so "retry the
    # key recovering soonest" and "retry the key we happened to try last" give
    # different answers and the assertion below can tell them apart.
    pool("sk-a,sk-b")
    client = ScriptedHeaders([
        (429, {"retry-after": "2"}),    # sk-a: rate limited, back in two
        (429, {}),                      # sk-b: no credits, cools for a minute
        (200, {}),                      # the retry, which must go to sk-a
    ])
    response = asyncio.run(
        api_keys.post(
            client, "https://example.test/v1/responses",
            provider=api_keys.OPENAI, headers={}, json={"model": "m"},
            retry_wait=8.0,
        )
    )
    assert response.status_code == 200
    # Waited for the two seconds, not the minute.
    assert len(slept) == 1 and 0 < slept[0] <= 2.0
    # And spent the wait on the key it was waiting for.
    assert client.bearers() == ["Bearer sk-a", "Bearer sk-b", "Bearer sk-a"]


def test_post_does_not_wait_longer_than_the_cap(pool, slept):
    """`MAX_RETRY_WAIT` holds even when the caller offers more than that.

    The budget is passed as a number far above the cap on purpose -- the two
    production callers pass `MAX_RETRY_WAIT` itself, so a cap that only worked
    because the caller asked for less would be no cap at all.
    """
    pool("sk-a,sk-b")
    seconds = str(int(api_keys.MAX_RETRY_WAIT) + 600)
    client = ScriptedHeaders([
        (429, {"retry-after": seconds}), (429, {"retry-after": seconds}),
    ])
    response = asyncio.run(
        api_keys.post(
            client, "https://example.test/v1/responses",
            provider=api_keys.OPENAI, headers={}, json={"model": "m"},
            retry_wait=float(seconds),
        )
    )
    assert response.status_code == 429
    assert slept == [] and len(client.sent) == 2


def test_post_does_not_wait_longer_than_the_caller_allows(pool, slept):
    # The other bound: the vendor's wait is inside the cap, but this caller
    # said it could only spare a second.
    pool("sk-a,sk-b")
    client = ScriptedHeaders([(429, {"retry-after": "5"}), (429, {"retry-after": "5"})])
    response = asyncio.run(
        api_keys.post(
            client, "https://example.test/v1/responses",
            provider=api_keys.OPENAI, headers={}, json={"model": "m"},
            retry_wait=1.0,
        )
    )
    assert response.status_code == 429
    assert slept == [] and len(client.sent) == 2


def test_post_without_a_budget_never_waits(pool, slept):
    """The four callers that pass no budget keep today's behaviour exactly.

    Stated as its own test because the wait is latency, and latency added to a
    path nobody asked to slow down is a regression even when it buys a
    success.
    """
    pool("sk-a,sk-b")
    client = ScriptedHeaders([(429, {"retry-after": "1"}), (429, {"retry-after": "1"})])
    assert post(client).status_code == 429
    assert slept == [] and len(client.sent) == 2


def test_post_does_not_wait_out_a_rejected_key(pool, slept):
    # A 401 is a key the provider will not accept at all; it is not coming
    # back in two seconds, and its cooldown is the long one.
    pool("sk-a,sk-b")
    client = ScriptedHeaders([(401, {"retry-after": "1"}), (401, {"retry-after": "1"})])
    response = asyncio.run(
        api_keys.post(
            client, "https://example.test/v1/responses",
            provider=api_keys.OPENAI, headers={}, json={"model": "m"},
            retry_wait=8.0,
        )
    )
    assert response.status_code == 401
    assert slept == [] and len(client.sent) == 2


# Which `api_keys.post` call sites ask to be held still, and why. The pool's
# own tests cover what a budget does; this covers whether the two callers that
# should have one still do -- a regression nothing else would notice, because
# losing the wait does not fail anything, it just quietly drops pairs again.
WAITING_CALLERS = {
    # Both callers of each are timer jobs (the sweep and the weekly digest),
    # so no HTTP response is held open. A first run reaches the scoring one
    # through a background task the client polls.
    "services/jobs/score.py",
    "services/jobs/reasons.py",
}


def test_only_the_timer_job_callers_wait_on_a_throttled_pool():
    """Fails if a `post` call site gains or loses `retry_wait`.

    **Do not make this pass by editing `WAITING_CALLERS` alone.** Adding a
    path here says "a person can wait seconds for this call". That is true of
    a timer job and false of anything behind a request someone is watching, so
    decide that first and record it after.
    """
    import re
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    waiting: set[str] = set()
    sites: set[str] = set()
    for path in app.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(app).as_posix()
        for match in re.finditer(r"api_keys\.post\(", text):
            sites.add(rel)
            # The call's own argument list, to its closing parenthesis.
            tail = text[match.end() : text.find("\n    )", match.end())]
            if "retry_wait" in tail:
                waiting.add(rel)
    # The five single-request callers named in this module's docstring.
    assert len(sites) == 5, sorted(sites)
    assert waiting == WAITING_CALLERS, sorted(waiting)


def test_post_refuses_to_send_a_request_with_no_credential(pool):
    """Every caller checks `configured` first, so this is a programming error
    -- and it must not go out as a request with no key, which reads as an
    authentication bug rather than a missing setting."""
    pool("")
    with pytest.raises(RuntimeError):
        post(RecordingClient([200]))


def test_post_does_not_retry_a_transport_error(pool):
    """A connection reset says nothing about the key, and retrying it on a
    second account would double a timeout the caller is already waiting out."""
    import httpx

    class Broken(RecordingClient):
        async def post(self, url, headers=None, json=None, **kwargs):
            self.sent.append({"url": url, "headers": dict(headers or {}), "body": json})
            raise httpx.ConnectError("boom")

    pool("sk-a,sk-b")
    client = Broken([])
    with pytest.raises(httpx.ConnectError):
        post(client)
    assert len(client.sent) == 1
    assert api_keys.cooling_down(api_keys.OPENAI) == 0


# --------------------------------------------------------------------------
# The chat adapters, through a real turn
# --------------------------------------------------------------------------


class RotatingChatClient:
    """A stub `httpx.AsyncClient` whose stream answers a scripted status list.

    Records the credential header of every attempt, which is the only way to
    see that the retry actually moved to a different key rather than retrying
    the same one.
    """

    def __init__(
        self,
        statuses: list[int],
        payloads: list[dict],
        auth_header: str,
        framer=sse_lines,
        error_body: dict | None = None,
    ):
        self.statuses = list(statuses)
        self.payloads = list(payloads)
        self.auth_header = auth_header
        # The two APIs frame their streams and their error bodies
        # differently, so the stub is handed each vendor's rather than
        # pretending one shape serves both.
        self.framer = framer
        self.error_body = error_body or {"error": {"message": "slow down"}}
        self.auth: list[str] = []
        self.requests: list[dict] = []
        self.decision_requests: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        self.decision_requests.append({"url": url, "body": json or {}})
        return ScriptedStream({"answers": []})

    def stream(self, method, url, headers=None, json=None, **kwargs):
        self.auth.append((headers or {}).get(self.auth_header))
        self.requests.append(json or {})
        status = self.statuses.pop(0) if self.statuses else 200
        if status >= 400:
            return ScriptedStream(self.error_body, status_code=status, lines=[])
        payload = self.payloads.pop(0)
        return ScriptedStream(payload, lines=self.framer(payload))


def run_turn(model: str) -> list[dict]:
    import app.services.llm as llm_module

    async def collect():
        return [
            event
            async for event in llm_module.aiter_llm_turn(
                [{"role": "user", "content": "go"}], "SRC", model=model, max_rounds=2
            )
        ]

    return asyncio.run(collect())


def test_a_throttled_openai_key_retries_the_round_on_the_next_one(pool, monkeypatch):
    pool("sk-a,sk-b")
    scripted = RotatingChatClient(
        [429, 200], [llm_message(content="Done.")], "Authorization"
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", scripted)

    events = run_turn("gpt-6.1-terra")

    assert [e["content"] for e in events if e.get("kind") == "assistant"] == ["Done."]
    assert scripted.auth == ["Bearer sk-a", "Bearer sk-b"]


def test_a_throttled_anthropic_key_retries_the_round_on_the_next_one(pool, monkeypatch):
    from tests.anthropic_script import anthropic_message, text_block
    from tests.anthropic_script import sse_lines as anthropic_sse_lines

    pool("", provider=api_keys.OPENAI)
    pool("sk-ant-1,sk-ant-2", provider=api_keys.ANTHROPIC)
    message = anthropic_message(blocks=[text_block("Done.")])
    scripted = RotatingChatClient(
        [503, 200],
        [message],
        "x-api-key",
        framer=anthropic_sse_lines,
        error_body={"type": "error", "error": {"message": "overloaded"}},
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", scripted)

    events = run_turn("claude-sonnet-5-5")

    assert [e["content"] for e in events if e.get("kind") == "assistant"] == ["Done."]
    assert scripted.auth == ["sk-ant-1", "sk-ant-2"]


def test_a_rotated_round_is_not_replayed_to_the_user(pool, monkeypatch):
    """The retry happens before the round yields anything, so the user never
    sees the first attempt's text twice."""
    pool("sk-a,sk-b")
    scripted = RotatingChatClient(
        [429, 200], [llm_message(content="Only once.")], "Authorization"
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", scripted)

    events = run_turn("gpt-6.1-terra")

    assert [e["content"] for e in events if e.get("kind") == "assistant"].count(
        "Only once."
    ) == 1


def test_one_key_still_surfaces_the_provider_error(pool, monkeypatch):
    """A pool of one must fail the way the single key used to: one attempt,
    and the provider's own message relayed as a 502."""
    from fastapi import HTTPException

    pool("sk-only")
    scripted = RotatingChatClient([429], [], "Authorization")
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", scripted)

    with pytest.raises(HTTPException) as caught:
        run_turn("gpt-6.1-terra")

    assert caught.value.status_code == 502
    assert "slow down" in str(caught.value.detail)
    assert len(scripted.auth) == 1


def test_an_exhausted_pool_surfaces_the_error_after_trying_every_key(pool, monkeypatch):
    from fastapi import HTTPException

    pool("sk-a,sk-b,sk-c")
    scripted = RotatingChatClient([429, 503, 429], [], "Authorization")
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", scripted)

    with pytest.raises(HTTPException) as caught:
        run_turn("gpt-6.1-terra")

    assert caught.value.status_code == 502
    assert scripted.auth == ["Bearer sk-a", "Bearer sk-b", "Bearer sk-c"]


def test_the_chat_picker_offers_a_provider_with_several_keys(pool, client: TestClient):
    """`configured` reads a list, so a pool is as good a credential as one key
    -- a provider whose keys are a comma-separated list must still be offered
    rather than filtered out of the picker."""
    pool("sk-a,sk-b")
    pool("", provider=api_keys.ANTHROPIC)
    from app.services import model_choice

    offered = {spec.provider for spec in model_choice.offered("guest")}
    assert offered == {api_keys.OPENAI}
