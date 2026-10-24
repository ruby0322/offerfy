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
    openai_model: str = "gpt-5.6-terra"
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
