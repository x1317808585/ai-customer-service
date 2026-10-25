from datetime import datetime
from typing import Annotated, Any, Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UserRole = Literal["customer", "admin"]
MessageRole = Literal["user", "assistant"]
OrderStatus = Literal["placed", "shipped", "delivered", "cancelled"]
ReturnStatus = Literal["pending", "completed", "rejected", "cancelled"]
TicketStatus = Literal["open", "closed"]
ActionKind = Literal["return_request"]
ActionStatus = Literal["pending", "executed", "cancelled", "failed"]

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

    # 这条消息是一张确认卡片时，对应的待确认操作
    action: Mapped["PendingAction | None"] = relationship(
        foreign_keys="PendingAction.card_message_id", viewonly=True, lazy="raise"
    )


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


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'rejected', 'cancelled')", name="ck_return_requests_status"
        ),
        CheckConstraint("quantity > 0", name="ck_return_requests_quantity"),
        CheckConstraint("refund_cents >= 0", name="ck_return_requests_refund_cents"),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)", name="ck_return_requests_resolved_at"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), index=True)
    quantity: Mapped[int]
    refund_cents: Mapped[int]
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[ReturnStatus] = mapped_column(String(16))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    order_item: Mapped[OrderItem] = relationship(lazy="raise")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_tickets_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    summary: Mapped[str] = mapped_column(String(1000))
    status: Mapped[TicketStatus] = mapped_column(String(16))
    created_at: Mapped[CreatedAt]


class PendingAction(Base):
    """助手提议、等待用户在页面上确认的业务写操作。确认接口是它唯一的执行入口。"""

    __tablename__ = "pending_actions"
    __table_args__ = (
        CheckConstraint("kind IN ('return_request')", name="ck_pending_actions_kind"),
        CheckConstraint(
            "status IN ('pending', 'executed', 'cancelled', 'failed')", name="ck_pending_actions_status"
        ),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)", name="ck_pending_actions_resolved_at"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    # 触发它的用户消息。唯一：一次回复最多发起一个待确认操作
    source_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    # 显示确认卡片的那条消息
    card_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    kind: Mapped[ActionKind] = mapped_column(String(32))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ActionStatus] = mapped_column(String(16))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]
