from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT_DIR = Path(__file__).resolve().parents[2]

LlmProvider = Literal["fake", "openai_compatible"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    frontend_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    jwt_secret: Annotated[SecretStr, Field(min_length=32)]
    access_token_minutes: int = 480
    cookie_secure: bool = False

    llm_provider: LlmProvider = "fake"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""

    @model_validator(mode="after")
    def require_settings_for_real_llm(self) -> Self:
        if self.llm_provider == "openai_compatible":
            required = {
                "LLM_BASE_URL": self.llm_base_url,
                "LLM_API_KEY": self.llm_api_key.get_secret_value(),
                "LLM_MODEL": self.llm_model,
            }
            missing = [name for name, value in required.items() if not value.strip()]
            if missing:
                raise ValueError(f"LLM_PROVIDER=openai_compatible 时缺少配置：{', '.join(missing)}")
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """读取并校验配置。同一个进程里只真正读取一次，之后直接复用。"""
    return Settings()
