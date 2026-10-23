import pytest
from pydantic import ValidationError

from app.config import Settings

SHORT_SECRET = "too-short-secret"


def make_settings(**overrides: str) -> Settings:
    """创建一份不读取 .env 的配置，只包含测试显式给出的值。"""
    values = {"postgres_user": "test", "postgres_password": "test", "postgres_db": "test"}
    return Settings(_env_file=None, **(values | overrides))


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)


def test_missing_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        make_settings()


def test_short_jwt_secret_is_rejected_without_leaking_it() -> None:
    with pytest.raises(ValidationError) as error:
        make_settings(jwt_secret=SHORT_SECRET)

    assert "jwt_secret" in str(error.value)
    assert SHORT_SECRET not in str(error.value)
