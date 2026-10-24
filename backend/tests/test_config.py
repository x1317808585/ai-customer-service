import pytest
from pydantic import ValidationError

from app.config import Settings

SECRET_SETTINGS = ("jwt_secret", "mcp_token_secret")
SHORT_SECRET = "too-short-secret"
VALID_SECRET = "a-valid-secret-that-is-at-least-32-chars"


def make_settings(**overrides: str) -> Settings:
    """创建一份不读取 .env 的配置，只包含测试显式给出的值。"""
    values = {"postgres_user": "test", "postgres_password": "test", "postgres_db": "test"}
    return Settings(_env_file=None, **(values | overrides))


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SECRET_SETTINGS:
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
