"""创建用户，或者给已有用户重置密码。密码在终端里输入，不会显示，也不会留在命令历史里。

用法（在 backend 目录下）：
    uv run python -m scripts.create_user alice                      创建顾客账号
    uv run python -m scripts.create_user admin --role admin         创建管理员账号
    uv run python -m scripts.create_user alice --reset-password     重置密码（角色不变）
"""

import argparse
import asyncio
import sys
from getpass import getpass
from typing import get_args

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.config import get_settings
from app.models import User, UserRole

MIN_PASSWORD_LENGTH = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="创建用户或重置密码")
    parser.add_argument("username")
    parser.add_argument("--role", choices=get_args(UserRole), help="新用户的角色，默认 customer")
    parser.add_argument("--reset-password", action="store_true", help="给已有用户重置密码")
    args = parser.parse_args()
    if args.reset_password and args.role:
        parser.error("重置密码时不能修改角色")
    return args


def ask_password() -> str:
    password = getpass("输入密码：")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")
    if getpass("再输入一次：") != password:
        sys.exit("两次输入的密码不一致")
    return password


async def main() -> None:
    args = parse_args()
    engine = create_async_engine(get_settings().database_url)
    async with async_sessionmaker(engine)() as session:
        user = await session.scalar(select(User).where(User.username == args.username))

        if args.reset_password:
            if user is None:
                sys.exit(f"用户 {args.username} 不存在")
            user.password_hash = hash_password(ask_password())
            message = f"已重置 {args.username} 的密码（角色仍为 {user.role}）"
        else:
            if user is not None:
                sys.exit(f"用户 {args.username} 已存在。如需重置密码，请加上 --reset-password")
            role = args.role or "customer"
            session.add(User(username=args.username, role=role, password_hash=hash_password(ask_password())))
            message = f"已创建用户 {args.username}（角色 {role}）"

        await session.commit()
    await engine.dispose()
    print(message)


if __name__ == "__main__":
    asyncio.run(main())
