from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """取出应用启动时创建的 Session 工厂。"""
    return request.app.state.session_factory


SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def get_session(session_factory: SessionFactoryDep) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
