"""放入演示用的商品和订单，方便手动体验，以后客服 Agent 也查这些数据。

可以重复运行：同名商品已存在就跳过；用户已经有订单就不再添加演示订单。
不删除任何数据，聊天记录不受影响。

用法（在 backend 目录下，先用 create_user 创建好用户）：
    uv run python -m scripts.seed_demo_data alice
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Order, OrderItem, OrderStatus, Product, User


@dataclass(frozen=True)
class DemoProduct:
    name: str
    description: str
    price_cents: int
    stock: int
    returnable: bool = True


@dataclass(frozen=True)
class DemoOrder:
    status: OrderStatus
    items: dict[str, int]  # 商品名 -> 数量
    delivered_days_ago: int | None = None


DEMO_PRODUCTS = [
    DemoProduct("SoundPods 降噪耳机", "主动降噪蓝牙耳机，续航 30 小时，支持无线充电", 59900, 20),
    DemoProduct("USB-C 快充线 1 米", "支持 60W 快充，编织线身", 3900, 200),
    DemoProduct("65W 氮化镓充电器", "双 USB-C 口，可同时给笔记本和手机充电", 14900, 50),
    DemoProduct("机械键盘 K8", "热插拔茶轴，有线/蓝牙双模", 39900, 8),
    DemoProduct("定制刻字保温杯", "500ml 不锈钢，杯身可刻字，定制商品不支持退货", 12900, 30, returnable=False),
    DemoProduct("4K 显示器 27 英寸", "IPS 面板，Type-C 一线连接", 189900, 0),
]

DEMO_ORDERS = [
    DemoOrder("delivered", {"SoundPods 降噪耳机": 1, "USB-C 快充线 1 米": 2}, delivered_days_ago=2),
    DemoOrder("delivered", {"定制刻字保温杯": 1}, delivered_days_ago=3),
    DemoOrder("delivered", {"机械键盘 K8": 1}, delivered_days_ago=15),
    DemoOrder("shipped", {"65W 氮化镓充电器": 1}),
    DemoOrder("placed", {"USB-C 快充线 1 米": 1}),
]


@dataclass(frozen=True)
class SeedResult:
    created_products: int
    created_orders: int


async def seed_demo_data(session_factory: async_sessionmaker[AsyncSession], username: str) -> SeedResult:
    async with session_factory() as session, session.begin():
        user = await session.scalar(select(User).where(User.username == username))
        if user is None:
            raise LookupError(f"用户 {username} 不存在，请先运行 scripts.create_user")

        products = {product.name: product for product in await session.scalars(select(Product))}
        missing = [demo for demo in DEMO_PRODUCTS if demo.name not in products]
        for demo in missing:
            product = Product(
                name=demo.name,
                description=demo.description,
                price_cents=demo.price_cents,
                stock=demo.stock,
                returnable=demo.returnable,
            )
            session.add(product)
            products[demo.name] = product
        await session.flush()  # 让新商品拿到 id，下面的订单项要用

        has_orders = await session.scalar(select(Order.id).where(Order.user_id == user.id).limit(1))
        if has_orders is not None:
            return SeedResult(created_products=len(missing), created_orders=0)

        now = datetime.now(UTC)
        for demo in DEMO_ORDERS:
            # 演示订单当作历史数据直接写入，不经过下单流程，也不扣库存。
            items = [
                OrderItem(
                    product_id=products[name].id,
                    product_name=name,
                    unit_price_cents=products[name].price_cents,
                    quantity=quantity,
                )
                for name, quantity in demo.items.items()
            ]
            delivered_at = None if demo.delivered_days_ago is None else now - timedelta(days=demo.delivered_days_ago)
            session.add(
                Order(
                    user_id=user.id,
                    status=demo.status,
                    total_cents=sum(item.unit_price_cents * item.quantity for item in items),
                    delivered_at=delivered_at,
                    items=items,
                )
            )
        return SeedResult(created_products=len(missing), created_orders=len(DEMO_ORDERS))


async def main() -> None:
    parser = argparse.ArgumentParser(description="放入演示用的商品和订单")
    parser.add_argument("username", help="演示订单属于哪个用户")
    args = parser.parse_args()

    engine = create_async_engine(get_settings().database_url)
    try:
        result = await seed_demo_data(async_sessionmaker(engine, expire_on_commit=False), args.username)
    except LookupError as error:
        sys.exit(str(error))
    finally:
        await engine.dispose()
    print(f"新增商品 {result.created_products} 个，新增 {args.username} 的订单 {result.created_orders} 个")


if __name__ == "__main__":
    asyncio.run(main())
