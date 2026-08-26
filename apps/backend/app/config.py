from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env (same file docker compose reads). Not apps/backend/.env.
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
    typst_package_path: str | None = None
    typst_font_paths: str | None = None
    auth_token_secret: str = "dev-insecure-change-me"
    app_env: str = "development"
    google_redirect_uri: str | None = None

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
