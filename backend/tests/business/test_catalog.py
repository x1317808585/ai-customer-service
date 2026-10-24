from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.catalog import Catalog, ProductNotFoundError

AddProduct = Callable[..., Awaitable[int]]


@pytest.fixture
def catalog(session_factory: async_sessionmaker[AsyncSession]) -> Catalog:
    return Catalog(session_factory)


async def test_search_matches_name_case_insensitively(catalog: Catalog, add_product: AddProduct) -> None:
    await add_product("SoundPods 降噪耳机")
    await add_product("USB-C 充电线")

    results = await catalog.search_products("soundpods")

    assert [product.name for product in results] == ["SoundPods 降噪耳机"]


async def test_search_treats_wildcards_as_plain_text(catalog: Catalog, add_product: AddProduct) -> None:
    await add_product("SoundPods 降噪耳机")

    assert await catalog.search_products("%") == []


async def test_get_product_returns_price_and_stock(catalog: Catalog, add_product: AddProduct) -> None:
    product_id = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=3, returnable=False)

    product = await catalog.get_product(product_id)

    assert (product.name, product.price_cents, product.stock, product.returnable) == (
        "SoundPods 降噪耳机",
        59900,
        3,
        False,
    )


async def test_unknown_product_is_not_found(catalog: Catalog) -> None:
    with pytest.raises(ProductNotFoundError):
        await catalog.get_product(999)
