from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Product

AddProduct = Callable[..., Awaitable[int]]


@pytest.fixture
def add_product(session_factory: async_sessionmaker[AsyncSession]) -> AddProduct:
    """往测试库里加一个商品，返回它的 id。"""

    async def _add_product(
        name: str, price_cents: int = 1000, stock: int = 10, returnable: bool = True
    ) -> int:
        async with session_factory() as session:
            product = Product(
                name=name,
                description=f"{name}的商品说明",
                price_cents=price_cents,
                stock=stock,
                returnable=returnable,
            )
            session.add(product)
            await session.commit()
            return product.id

    return _add_product
