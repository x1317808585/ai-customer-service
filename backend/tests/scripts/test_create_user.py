"""create_user --if-missing 遇到已有账号：角色一致就跳过，不一致就报错，从不改密码和角色。"""

import pytest

from app.models import User
from scripts.create_user import existing_user_message


def test_existing_user_with_same_role_is_skipped() -> None:
    user = User(username="alice", role="customer", password_hash="unchanged")

    assert "跳过" in existing_user_message(user, "customer")
    assert user.password_hash == "unchanged"


def test_existing_user_with_another_role_stops_with_error() -> None:
    user = User(username="alice", role="customer", password_hash="unchanged")

    with pytest.raises(SystemExit) as stopped:
        existing_user_message(user, "admin")

    assert "不会修改角色" in str(stopped.value.code)
    assert user.role == "customer"
