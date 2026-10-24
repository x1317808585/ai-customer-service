from datetime import datetime
from typing import Annotated, Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UserRole = Literal["customer", "admin"]
MessageRole = Literal["user", "assistant"]
OrderStatus = Literal["placed", "shipped", "delivered", "cancelled"]

CreatedAt = Annotated[
    datetime,
    mapped_column(DateTime(timezone=True), server_default=func.now()),
]


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('customer', 'admin')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(String(16))
    created_at: Mapped[CreatedAt]


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[CreatedAt]


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[CreatedAt]


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="ck_products_price_cents"),
        CheckConstraint("stock >= 0", name="ck_products_stock"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int]
    stock: Mapped[int]
    returnable: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[CreatedAt]


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('placed', 'shipped', 'delivered', 'cancelled')", name="ck_orders_status"
        ),
        CheckConstraint("total_cents >= 0", name="ck_orders_total_cents"),
        CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)", name="ck_orders_delivered_at"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(String(16))
    total_cents: Mapped[int]
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    items: Mapped[list["OrderItem"]] = relationship(order_by="OrderItem.id", lazy="raise")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity"),
        CheckConstraint("unit_price_cents >= 0", name="ck_order_items_unit_price_cents"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    product_name: Mapped[str] = mapped_column(String(100))
    unit_price_cents: Mapped[int]
    quantity: Mapped[int]
