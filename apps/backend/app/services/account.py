"""The account profile: what a person may say about themselves, and how.

Separate from app.services.profile, which builds the *talent* profile an
employer reads. That one is derived from a resume by a model and is deleted the
moment the matching consent is withdrawn. This one is six columns the person
typed, kept as account settings.

Everything here normalises before it validates, so that a trailing space or a
pasted "  Taipei " is accepted rather than refused on a technicality, and so
that what we store is what we would have asked them to type. An empty string
means "clear this", which is distinct from the field being absent -- see
`FIELDS` and the router's use of `exclude_unset`.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import User

# Length caps, in characters. They match the column widths in models.User --
# a cap only enforced in Python turns a long paste into a database error.
USERNAME_MIN = 3
USERNAME_MAX = 30
DISPLAY_NAME_MAX = 80
HEADLINE_MAX = 120
LOCATION_MAX = 80
WEBSITE_MAX = 255
BIO_MAX = 600

# Lowercase, starts and ends with a letter or digit, underscores inside. No
# dots and no hyphens: a handle that may one day appear in a path or a
# subdomain should not need escaping to get there, and `.` in particular would
# collide with file extensions.
_USERNAME_RE = re.compile(rf"^[a-z0-9][a-z0-9_]{{{USERNAME_MIN - 2},{USERNAME_MAX - 2}}}[a-z0-9]$")

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

# Held back because a handle is a public-looking identifier and these either
# already name a route on the site or would be read as Offerfy speaking. The
# list is small on purpose: reserving broadly is how you tell a legitimate
# `security` or `jobs` user they cannot have their own name.
RESERVED_USERNAMES = frozenset(
    {
        "about",
        "admin",
        "administrator",
        "api",
        "blog",
        "contact",
        "create",
        "dashboard",
        "editor",
        "help",
        "jobs",
        "login",
        "logout",
        "new",
        "offerfy",
        "plan",
        "pricing",
        "privacy",
        "root",
        "settings",
        "support",
        "system",
        "terms",
        "upload",
        "usage",
        "user",
        "www",
    }
)

# The columns this service owns. Ordered as the form shows them.
FIELDS = ("username", "display_name", "headline", "location", "website", "bio")


class AccountError(ValueError):
    """A rejected field, with the machine-readable reason.

    `code` rather than a sentence: the client renders the message in the
    person's own locale, and the three locales are maintained in
    messages/*.json rather than here.
    """

    def __init__(self, field: str, code: str):
        super().__init__(f"{field}: {code}")
        self.field = field
        self.code = code


def _collapse(raw: str) -> str:
    """One line, single-spaced, no control characters.

    NFC first: "台北" typed on macOS arrives decomposed, and two spellings of
    the same string are two different values to a uniqueness check and to a
    `==` in a test.
    """
    text = unicodedata.normalize("NFC", raw)
    text = "".join(" " if ch in "\t\r\n" else ch for ch in text if ch.isprintable() or ch in "\t\r\n")
    return re.sub(r"\s+", " ", text).strip()


def normalize_username(raw: str) -> str | None:
    """Lowercased and checked, or None for "clear it".

    Case is folded rather than refused: someone typing `Ruby` means `ruby`,
    and storing both would let two accounts hold handles that read the same.
    """
    text = _collapse(raw).lower()
    if not text:
        return None
    if len(text) < USERNAME_MIN or len(text) > USERNAME_MAX:
        raise AccountError("username", "length")
    if not _USERNAME_RE.match(text):
        raise AccountError("username", "charset")
    if text in RESERVED_USERNAMES:
        raise AccountError("username", "reserved")
    return text


def _normalize_line(field: str, raw: str, limit: int) -> str | None:
    text = _collapse(raw)
    if not text:
        return None
    if len(text) > limit:
        raise AccountError(field, "length")
    return text


def normalize_website(raw: str) -> str | None:
    """A full http(s) URL, with the scheme filled in if they left it out.

    People type `example.com`, so assuming https beats refusing. Anything that
    is not http(s) is refused rather than coerced: `javascript:` and `data:`
    are the reason this is validated at all, since the value is rendered as a
    link.
    """
    text = _collapse(raw).replace(" ", "")
    if not text:
        return None
    # Match any scheme, not just `//`-style ones, so that `javascript:alert(1)`
    # is refused as a scheme rather than having https:// pasted in front of it
    # and then failing the host check for the wrong reason.
    marker = _SCHEME_RE.match(text)
    if marker is None:
        text = f"https://{text}"
    elif "://" not in text:
        raise AccountError("website", "scheme")
    scheme, _, rest = text.partition("://")
    if scheme.lower() not in ("http", "https"):
        raise AccountError("website", "scheme")
    host = rest.split("/", 1)[0].split("@")[-1]
    # A dotted host with no spaces. Not a full URL parse -- this only has to
    # reject what is obviously not a web address.
    if "." not in host or host.startswith(".") or host.endswith("."):
        raise AccountError("website", "host")
    text = f"{scheme.lower()}://{rest}"
    if len(text) > WEBSITE_MAX:
        raise AccountError("website", "length")
    return text


def normalize_bio(raw: str) -> str | None:
    """Multi-line, unlike every other field. Blank runs collapsed to one."""
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch.isprintable())
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return None
    if len(text) > BIO_MAX:
        raise AccountError("bio", "length")
    return text


def normalize(field: str, raw: str) -> str | None:
    if field == "username":
        return normalize_username(raw)
    if field == "website":
        return normalize_website(raw)
    if field == "bio":
        return normalize_bio(raw)
    limits = {
        "display_name": DISPLAY_NAME_MAX,
        "headline": HEADLINE_MAX,
        "location": LOCATION_MAX,
    }
    return _normalize_line(field, raw, limits[field])


def username_taken(db: Session, username: str, *, exclude_user_id: str) -> bool:
    """Case-insensitively, because normalize_username has already lowercased.

    func.lower on the column as well as the value, so a row written before
    normalisation existed still collides rather than hiding behind its case.
    """
    row = db.execute(
        select(User.id)
        .where(func.lower(User.username) == username)
        .where(User.id != exclude_user_id)
        .limit(1)
    ).first()
    return row is not None


def apply(db: Session, user: User, updates: dict[str, str]) -> User:
    """Normalise, validate, then assign. Nothing is written on a refusal.

    Two passes rather than one: assigning as we go would leave a valid
    `display_name` on the session when a later `website` is refused, and the
    request's commit would persist half the form.
    """
    cleaned: dict[str, str | None] = {}
    for field, raw in updates.items():
        if field not in FIELDS:
            raise AccountError(field, "unknown")
        cleaned[field] = normalize(field, raw if raw is not None else "")

    new_username = cleaned.get("username")
    if new_username and new_username != (user.username or "").lower():
        if username_taken(db, new_username, exclude_user_id=user.id):
            raise AccountError("username", "taken")

    for field, value in cleaned.items():
        setattr(user, field, value)
    return user


def display_name_of(user: User) -> str | None:
    """The name to show for this person, in order of who said it.

    Their own edit first, then what Google asserted. Email is deliberately not
    a fallback: showing `someone@gmail.com` where a name belongs is worse than
    showing nothing, and the callers that want the email already have it.
    """
    return (user.display_name or "").strip() or (user.name or "").strip() or None
