"""Several credentials per provider, and which one the next call uses.

`OPENAI_API_KEY` and `ANTHROPIC_API_KEY` each hold a *list*: one key, or
several separated by commas or whitespace. One key is the same thing as a pool
of one, so a deployment that never sets a second behaves exactly as it did
before this module existed.

What a pool buys is the thing a single key cannot do -- survive its own rate
limit. Both vendors meter per key, so a burst that would 429 one account
spreads over however many are configured; and a key being rotated out can sit
in the list while its replacement takes over, instead of being swapped by an
edit that has to land between two requests.

The policy, deliberately narrow:

  * **The first healthy key wins.** Not round-robin. Both vendors cache prompt
    prefixes per key and the chat turn re-sends a growing transcript every
    round, so spreading calls across keys by default would turn cache reads
    into fresh input and cost more than the throttling it avoided. Keys after
    the first are failover, not load balancing.

  * **A fault cools a key down, it does not retire it.** A 429 or a 5xx is
    about this credential at this moment, so the call moves to the next key
    and the failed one is skipped for `COOLDOWN_THROTTLED` seconds, or for
    whatever `Retry-After` asked for. A 401/403 is a key that is probably
    already gone -- the half-finished side of a rotation -- so it gets the
    much longer `COOLDOWN_REJECTED` instead of being retried once a minute.

  * **Nothing is skipped into an outage.** When every key is cooling down
    `rotation` still returns them all, soonest-to-recover first. So a pool of
    one behaves exactly like today's single key: one attempt, and the
    provider's own error surfaced to the caller.

Transport failures are not a credential fault and do not rotate. A connection
reset says nothing about the key, and retrying on a second account would
double a timeout the caller is already waiting out.

The cooldown table is per process and in memory. The public API runs two
uvicorn workers (AGENTS.md §2), so each learns about a throttled key
separately -- that costs one extra 429 per worker and needs no shared state
that could go wrong. Keys are identified in logs by their 1-based slot in the
configured list and by a short digest, never by the secret.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

OPENAI = "openai"
ANTHROPIC = "anthropic"

# Statuses that mean "this credential, right now" rather than "this request".
# 529 is Anthropic's overloaded; 408/425 are the two retry-safe client codes a
# proxy in front of either API is known to return.
THROTTLED = frozenset({408, 425, 429, 500, 502, 503, 504, 529})
# A credential the provider will not accept at all. Rotating past one is the
# whole point of keeping several: the old key of a half-finished rotation
# answers like this, and the next key in the list is the new one.
REJECTED = frozenset({401, 403})

COOLDOWN_THROTTLED = 60.0
COOLDOWN_REJECTED = 900.0
# A `Retry-After` is a hint from the vendor, not an instruction we follow off
# a cliff: a proxy that returns a day's worth of seconds must not take a key
# out of the pool until the process restarts.
MAX_COOLDOWN = 900.0

# How each vendor wants its credential presented. Anthropic takes `x-api-key`,
# not a bearer token -- an Authorization header there is only correct for an
# OAuth access token.
_SCHEME: dict[str, tuple[str, str]] = {
    OPENAI: ("Authorization", "Bearer {}"),
    ANTHROPIC: ("x-api-key", "{}"),
}

# Commas are the documented separator; whitespace is accepted too because an
# env file edited by hand tends to acquire it, and a key with a stray space
# around it would otherwise fail authentication for no visible reason.
_SEPARATORS = re.compile(r"[,\s]+")

_lock = threading.Lock()
# fingerprint -> the monotonic deadline before which this key is skipped.
_cooling: dict[str, float] = {}


@dataclass(frozen=True)
class Credential:
    """One key out of a provider's pool, with its place in it.

    `slot` and `fingerprint` exist so a throttled key can be named in a log
    line without the secret going anywhere near it. `total` rides along so a
    log line says "2 of 3" rather than leaving the reader to guess how much
    failover was left.
    """

    provider: str
    secret: str
    slot: int
    total: int

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.provider, self.secret)

    def header(self) -> dict[str, str]:
        name, template = _SCHEME.get(self.provider, _SCHEME[OPENAI])
        return {name: template.format(self.secret)}

    def __str__(self) -> str:
        return f"{self.provider} key {self.slot}/{self.total} ({self.fingerprint})"


def _fingerprint(provider: str, secret: str) -> str:
    """A key's name in a log line. Short, stable, and not the key.

    Scoped by provider so that the same string set as both vendors' key -- a
    thing that happens on a misconfigured host -- does not share one cooldown
    entry and make a throttled OpenAI key look like a throttled Anthropic one.
    """
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
    return f"{provider}:{digest}"


def _raw(provider: str) -> str:
    """The configured value for one provider.

    Written out rather than reached through getattr so that a provider name
    this module does not know about is an empty pool -- and so the settings
    fields stay greppable from here.
    """
    settings = get_settings()
    if provider == OPENAI:
        return settings.openai_api_key or ""
    if provider == ANTHROPIC:
        return settings.anthropic_api_key or ""
    return ""


def keys(provider: str) -> tuple[str, ...]:
    """Every key configured for `provider`, in the order it was written.

    Read fresh from settings on every call rather than parsed once at import:
    `get_settings` is itself cached, so this is a split of a short string, and
    the test suite changes the environment and clears that cache between
    cases.

    Duplicates are dropped. A list that names the same key twice is a copy and
    paste, not a deliberate weighting, and keeping it would make a cooled-down
    key appear healthy at its second position.
    """
    seen: set[str] = set()
    found: list[str] = []
    for candidate in _SEPARATORS.split(_raw(provider)):
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        found.append(candidate)
    return tuple(found)


def configured(provider: str) -> bool:
    """Whether there is any credential for this provider.

    The replacement for `bool((settings.openai_api_key or "").strip())`, which
    read a one-key world correctly and a list of keys only by accident.
    """
    return bool(keys(provider))


def count(provider: str) -> int:
    return len(keys(provider))


def should_rotate(status: int) -> bool:
    return status in THROTTLED or status in REJECTED


def rotation(provider: str) -> tuple[Credential, ...]:
    """The keys to try, best first.

    Healthy keys in configured order, then any that are cooling down, soonest
    to recover first. Cooling keys are included rather than withheld because
    an exhausted pool must degrade into the provider's own error -- the same
    error a single key would have produced -- and not into a 503 this module
    invented.
    """
    pool = keys(provider)
    if not pool:
        return ()
    total = len(pool)
    now = time.monotonic()
    with _lock:
        deadlines = {
            fingerprint: deadline
            for fingerprint, deadline in _cooling.items()
            if deadline > now
        }
        _cooling.clear()
        _cooling.update(deadlines)
    healthy: list[Credential] = []
    cooling: list[tuple[float, Credential]] = []
    for index, secret in enumerate(pool, start=1):
        credential = Credential(
            provider=provider, secret=secret, slot=index, total=total
        )
        deadline = deadlines.get(credential.fingerprint)
        if deadline is None:
            healthy.append(credential)
        else:
            cooling.append((deadline, credential))
    cooling.sort(key=lambda pair: (pair[0], pair[1].slot))
    return tuple(healthy) + tuple(credential for _, credential in cooling)


def _retry_after_seconds(value: Any) -> float | None:
    """`Retry-After` as seconds, or None if it is not a number of them.

    The header may also be an HTTP date, which is deliberately not parsed: the
    default cooldown is the right order of magnitude either way, and a date
    misread as a duration is how a key leaves the pool for a week.
    """
    if value is None:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return seconds


def penalize(
    credential: Credential,
    *,
    status: int | None = None,
    retry_after: Any = None,
) -> float:
    """Skip this key for a while. Returns the cooldown actually applied.

    Called by whoever saw the response, not from inside this module, because
    only the caller knows whether the status it got was about the credential
    or about the request it sent.
    """
    seconds = _retry_after_seconds(retry_after)
    if seconds is None:
        seconds = (
            COOLDOWN_REJECTED
            if status is not None and status in REJECTED
            else COOLDOWN_THROTTLED
        )
    seconds = max(1.0, min(seconds, MAX_COOLDOWN))
    with _lock:
        _cooling[credential.fingerprint] = time.monotonic() + seconds
    logger.warning(
        "api key cooling down provider=%s slot=%s/%s digest=%s status=%s seconds=%s",
        credential.provider,
        credential.slot,
        credential.total,
        credential.fingerprint,
        status,
        int(seconds),
    )
    return seconds


def cooling_down(provider: str) -> int:
    """How many of this provider's keys are being skipped right now.

    Nothing surfaces this yet; it is here for the tests and for whoever adds
    it to the admin overview, where it belongs for the same reason the spend
    ceilings are there -- a pool quietly down to its last key is an outage
    waiting for the week's traffic.
    """
    now = time.monotonic()
    pool = keys(provider)
    with _lock:
        snapshot = dict(_cooling)
    return sum(
        1 for secret in pool if snapshot.get(_fingerprint(provider, secret), 0.0) > now
    )


def reset() -> None:
    """Forget every cooldown. For tests, which share one process."""
    with _lock:
        _cooling.clear()


async def post(
    client: httpx.AsyncClient,
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
    json: Any = None,
) -> httpx.Response:
    """One POST, carrying the provider's credential, failing over on a fault.

    Returns the last response it got, whatever it says -- the callers all have
    their own handling for a provider error, and replacing it with an
    exception here would mean rewriting five of them. Transport errors are
    raised through untouched, for the reason in the module docstring.

    Only for a single request/response. The streaming chat path cannot use
    this (its failure arrives inside a context manager it has to keep open),
    so each provider adapter runs the same loop itself over `rotation`.
    """
    pool = rotation(provider)
    if not pool:
        # Every caller checks `configured` first, so this is a programming
        # error rather than a deployment one -- and it must not be a request
        # sent with no credential at all, which reads as an auth bug.
        raise RuntimeError(f"no api key configured for provider {provider!r}")
    response: httpx.Response | None = None
    for index, credential in enumerate(pool):
        response = await client.post(
            url, headers={**(headers or {}), **credential.header()}, json=json
        )
        if not should_rotate(response.status_code):
            return response
        penalize(
            credential,
            status=response.status_code,
            retry_after=response.headers.get("retry-after"),
        )
        if index + 1 < len(pool):
            logger.info(
                "retrying %s on the next key after status=%s",
                provider,
                response.status_code,
            )
    assert response is not None  # pool is non-empty, so the loop ran
    return response
