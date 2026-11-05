import pytest
from pydantic import ValidationError

from app.config import Settings

SECRET_SETTINGS = ("jwt_secret", "mcp_token_secret")
RETRIEVAL_SETTINGS = ("retrieval_mode", "embedding_model_dir")
SHORT_SECRET = "too-short-secret"
VALID_SECRET = "a-valid-secret-that-is-at-least-32-chars"
OTHER_VALID_SECRET = "another-valid-secret-at-least-32-chars"


def make_settings(**overrides: str) -> Settings:
    """创建一份不读取 .env 的配置，只包含测试显式给出的值。"""
    values = {"postgres_user": "test", "postgres_password": "test", "postgres_db": "test"}
    return Settings(_env_file=None, **(values | overrides))


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*SECRET_SETTINGS, *RETRIEVAL_SETTINGS):
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.parametrize("name", SECRET_SETTINGS)
def test_missing_secret_is_rejected(name: str) -> None:
    others = {other: VALID_SECRET for other in SECRET_SETTINGS if other != name}

    with pytest.raises(ValidationError, match=name):
        make_settings(**others)


@pytest.mark.parametrize("name", SECRET_SETTINGS)
def test_short_secret_is_rejected_without_leaking_it(name: str) -> None:
    others = {other: VALID_SECRET for other in SECRET_SETTINGS if other != name}

    with pytest.raises(ValidationError) as error:
        make_settings(**others, **{name: SHORT_SECRET})

    assert name in str(error.value)
    assert SHORT_SECRET not in str(error.value)


def test_the_two_secrets_must_differ() -> None:
    with pytest.raises(ValidationError, match="MCP_TOKEN_SECRET 不能和 JWT_SECRET 相同") as error:
        make_settings(jwt_secret=VALID_SECRET, mcp_token_secret=VALID_SECRET)

    assert VALID_SECRET not in str(error.value)


def test_keyword_retrieval_is_the_default_and_needs_no_model() -> None:
    settings = make_settings(jwt_secret=VALID_SECRET, mcp_token_secret=OTHER_VALID_SECRET)

    assert settings.retrieval_mode == "keyword"


def test_hybrid_retrieval_requires_the_model_directory() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_MODEL_DIR"):
        make_settings(jwt_secret=VALID_SECRET, mcp_token_secret=OTHER_VALID_SECRET, retrieval_mode="hybrid")
