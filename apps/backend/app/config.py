from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Dest / compose read repo-root .env. Public systemd injects .env.production
# (process env overrides this file). Not apps/backend/.env.
# Public Next /api rewrites are a separate footgun: they are baked at
# `next build` from BACKEND_INTERNAL_URL (see docs/deploy.md).
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "sqlite:///:memory:"
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    # Anthropic is the second provider; see app.services.llm.providers. Both
    # keys are optional and independent: a deployment with only one set gets a
    # working picker with a shorter list, because
    # services.model_choice.offered() filters out models whose provider has no
    # credential rather than offering one that would 502.
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    # The fallback model, for a caller who has chosen nothing. Normally left
    # unset so app.services.model_catalog.DEFAULT_MODEL decides -- the
    # catalogue is where a model's price and label live, so letting it own the
    # default too means one place to change. Set this only to pin a model the
    # catalogue does not list, which is how a proxy serving its own ids is
    # reached; such a model is priced as the dearest row in the catalogue.
    #
    # Replaces `OPENAI_MODEL`, which is deliberately no longer read: with two
    # providers a setting named after one of them could not name a model from
    # the other, so honouring it would have meant a config key that silently
    # worked for half the catalogue. The host's .env/.env.production were
    # migrated to CHAT_MODEL on 2026-10-29; `extra="ignore"` above means a
    # stale OPENAI_MODEL elsewhere is inert rather than an error.
    chat_model: str | None = None
    # read_layout's vision questions. The Decisions endpoint takes this
    # model only; it is a setting so a rename at GA is a config change.
    openai_decisions_model: str = "gpt-6-luna"
    typst_package_path: str | None = None
    typst_font_paths: str | None = None
    auth_token_secret: str = "dev-insecure-change-me"
    app_env: str = "development"
    google_redirect_uri: str | None = None
    admin_emails: str = ""
    # Weekly AI usage tiers (app.services.usage). None follows app_env, so
    # production enforces and development does not; set it explicitly to
    # exercise the limits on staging without pretending to be production.
    usage_limits_enabled: bool | None = None
    # Instance-wide weekly spend ceilings, micro-USD, 0 to disable. Derived
    # from the exposure table in the usage-policy spec, not from a budget --
    # revisit both against a real one. A ceiling nobody is told about is an
    # outage, so both are surfaced on the admin overview.
    global_weekly_ceiling_uusd: int = 150_000_000
    guest_weekly_ceiling_uusd: int = 40_000_000

    def pinned_chat_model(self) -> str | None:
        """The operator's model pin, or None to let the catalogue decide.

        An accessor rather than reading the field, so that "unset and blank
        mean the same thing" is written down once -- `CHAT_MODEL=` in an env
        file is how the host says "no pin", and a caller comparing the field
        to None would read that empty string as a model name.

        Returning None rather than a default keeps the catalogue the single
        source of that default; a second copy here is how the published model
        and the called one come to disagree.
        """
        pinned = (self.chat_model or "").strip()
        return pinned or None

    def s3_configured(self) -> bool:
        return all(
            [
                (self.s3_endpoint or "").strip(),
                (self.s3_access_key or "").strip(),
                (self.s3_secret_key or "").strip(),
                (self.s3_bucket or "").strip(),
            ]
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
