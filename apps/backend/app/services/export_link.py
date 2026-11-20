"""Short-lived signed links to one resume's PDF, for the agent surface.

Deliberately NOT a `ResumeShare` token. That one is a state the user chose to be
in: it has a table, a revoke path, an OG image and a relationship to the talent
profile. This is a one-shot pipe: the token is `resume_id|expiry` signed with
`deps._sign` and nothing is stored. It carries no user id: ownership was checked
when `export_resume` minted it, and the link is bearer-style so a download
client needs no credential. Anyone holding it can fetch that one PDF until it
expires, which is why it is short.
"""

import time

from app.config import get_settings
from app.deps import _sign, _unsign

EXPORT_TTL_SECONDS = 600


def mint(resume_id: str, *, now: float | None = None) -> tuple[str, int]:
    """(token, expiry as epoch seconds)."""
    expiry = int((time.time() if now is None else now) + EXPORT_TTL_SECONDS)
    return _sign(f"{resume_id}|{expiry}", get_settings().auth_token_secret), expiry


def resume_id_for(token: str, *, now: float | None = None) -> str | None:
    """The resume id a token names, or None if it is forged, malformed or expired."""
    value = _unsign(token, get_settings().auth_token_secret)
    if value is None:
        return None
    parts = value.split("|")
    if len(parts) != 2 or not parts[0]:
        return None
    try:
        expiry = int(parts[1])
    except ValueError:
        return None
    if (time.time() if now is None else now) >= expiry:
        return None
    return parts[0]


def pdf_filename(title: str) -> str:
    stem = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in " -_") else "_" for ch in title
    ).strip("._ ") or "resume"
    return f"{stem}.pdf"
