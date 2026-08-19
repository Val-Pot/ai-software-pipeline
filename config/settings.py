from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    telegram_max_document_bytes: int = 50 * 1024 * 1024

    github_token: str = ""
    github_owner: str = ""
    github_repo: str = ""
    github_webhook_secret: str = ""

    # Official coding-agent login. Overridden by COPILOT_USERNAME.
    # A separate github-copilot[bot] comment account is an erroneous assumption.
    copilot_username: str = "copilot-swe-agent[bot]"
    coding_agent_stale_timeout_sec: int = 1800
    coding_agent_poll_interval_sec: int = 30
    coding_agent_poll_timeout_sec: int = 21600

    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    jobs_store_path: str = "data/jobs.json"

    @property
    def repository(self) -> str:
        return f"{self.github_owner}/{self.github_repo}"

    @property
    def allowed_user_ids(self) -> set[int]:
        if not self.telegram_allowed_user_ids.strip():
            return set()
        return {
            int(part.strip())
            for part in self.telegram_allowed_user_ids.split(",")
            if part.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
