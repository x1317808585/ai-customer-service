from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Product

SEARCH_LIMIT = 10


class ProductNotFoundError(Exception):
    def __init__(self, product_id: int) -> None:
        super().__init__(f"商品 {product_id} 不存在")
        self.product_id = product_id


class ProductView(BaseModel):
    """对外返回的商品信息。只读，改了也不会影响数据库。"""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    name: str
    description: str
    price_cents: int
    stock: int
    returnable: bool


class Catalog:
    """商品目录：搜索商品、查看商品详情。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def search_products(self, keyword: str) -> list[ProductView]:
        keyword = keyword.strip()
        async with self._session_factory() as session:
            products = await session.scalars(
                select(Product)
                .where(
                    or_(
                        Product.name.icontains(keyword, autoescape=True),
                        Product.description.icontains(keyword, autoescape=True),
                    )
                )
                .order_by(Product.id)
                .limit(SEARCH_LIMIT)
            )
            return [ProductView.model_validate(product) for product in products]

    async def get_product(self, product_id: int) -> ProductView:
        async with self._session_factory() as session:
            product = await session.get(Product, product_id)
            if product is None:
                raise ProductNotFoundError(product_id)
            return ProductView.model_validate(product)
