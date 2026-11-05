from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT_DIR = Path(__file__).resolve().parents[2]

LlmProvider = Literal["fake", "openai_compatible"]

# keyword：只用关键词检索；hybrid：关键词和 BGE-M3 向量两路召回，需要 EMBEDDING_MODEL_DIR
RetrievalMode = Literal["keyword", "hybrid"]


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

    backend_url: str = "http://localhost:8000"
    mcp_server_url: str = "http://localhost:8001/mcp"
    mcp_token_secret: Annotated[SecretStr, Field(min_length=32)]

    llm_provider: LlmProvider = "fake"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""

    retrieval_mode: RetrievalMode = "keyword"
    # BGE-M3 模型所在的目录，本机路径或容器里的路径都可以。只有 MCP Server 和导入知识库的脚本会读取它
    embedding_model_dir: str = ""

    @model_validator(mode="after")
    def require_different_secrets(self) -> Self:
        if self.jwt_secret.get_secret_value() == self.mcp_token_secret.get_secret_value():
            raise ValueError("MCP_TOKEN_SECRET 不能和 JWT_SECRET 相同")
        return self

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

    @model_validator(mode="after")
    def require_model_dir_for_hybrid_retrieval(self) -> Self:
        """这里只检查有没有填。目录是否存在、模型是否完整，在真正加载模型时检查。"""
        if self.retrieval_mode == "hybrid" and not self.embedding_model_dir.strip():
            raise ValueError("RETRIEVAL_MODE=hybrid 时缺少配置：EMBEDDING_MODEL_DIR（BGE-M3 模型所在的目录）")
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
