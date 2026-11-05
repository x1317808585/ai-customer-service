from datetime import datetime
from typing import Annotated, Any, Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UserRole = Literal["customer", "admin"]
MessageRole = Literal["user", "assistant"]
OrderStatus = Literal["placed", "shipped", "delivered", "cancelled"]
ReturnStatus = Literal["pending", "completed", "rejected", "cancelled"]
TicketStatus = Literal["open", "closed"]
ActionKind = Literal["order", "return_request", "return_cancellation", "ticket"]
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
    # 助手回复引用的知识库原文快照（出处和原文），没有引用时为空。知识库重建后也不变
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]

    # 这条消息是一张确认卡片时，对应的待确认操作
    action: Mapped["PendingAction | None"] = relationship(
        foreign_keys="PendingAction.card_message_id", viewonly=True, lazy="raise"
    )


class ConversationSummary(Base):
    """会话较早部分的摘要。每次重新摘要都新增一条，读取时只用最新的一条。"""

    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    # 摘要覆盖到这条消息为止（含）。之后的消息按原文交给模型
    covers_through_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    content: Mapped[str] = mapped_column(Text)
    # 被摘要覆盖的确认卡片对应的操作编号。只记编号，每轮按编号重新读取操作的最新状态
    action_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    created_at: Mapped[CreatedAt]


class UserMemory(Base):
    """顾客的一条长期偏好（固定的几个字段之一）。每个用户的每个字段只有一条，改口时覆盖。"""

    __tablename__ = "user_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "field", name="uq_user_memories_user_field"),
        CheckConstraint(
            "field IN ('earphone_fit', 'keyboard_switch', 'reply_detail')", name="ck_user_memories_field"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(32))
    # 最近一次说出这个偏好的消息。消息所在的会话删除后置空，偏好保留
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[CreatedAt]


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
        # 下面两条在迁移里以 NOT VALID 方式添加：历史记录没有处理人和原因，不回填，只约束新写入和被修改的行
        CheckConstraint("status = 'pending' OR resolved_by IS NOT NULL", name="ck_return_requests_resolved_by"),
        CheckConstraint(
            "status <> 'rejected' OR resolution_note IS NOT NULL", name="ck_return_requests_rejection_note"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), index=True)
    quantity: Mapped[int]
    refund_cents: Mapped[int]
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[ReturnStatus] = mapped_column(String(16))
    # 谁结束了这条申请：顾客撤回时是顾客本人，完成或拒绝时是管理员
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # 完成时可选的备注，拒绝时必填的原因。顾客能看到
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]

    order_item: Mapped[OrderItem] = relationship(lazy="raise")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_tickets_status"),
        # 待处理时三项处理信息都为空，关闭时三项都必须有
        CheckConstraint(
            "(status = 'open' AND resolution IS NULL AND resolved_by IS NULL AND resolved_at IS NULL)"
            " OR (status = 'closed' AND resolution IS NOT NULL"
            " AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_tickets_resolution",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    # 工单来自哪个会话（人工客服据此查看聊天记录）。会话删除后置空，工单保留
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    summary: Mapped[str] = mapped_column(String(1000))
    status: Mapped[TicketStatus] = mapped_column(String(16))
    # 关闭时由管理员填写的处理结果、处理人和处理时间。顾客能看到处理结果，看不到处理人
    resolution: Mapped[str | None] = mapped_column(String(2000))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]


class PendingAction(Base):
    """助手提议、等待用户在页面上确认的业务写操作。确认接口是它唯一的执行入口。"""

    __tablename__ = "pending_actions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('order', 'return_request', 'return_cancellation', 'ticket')", name="ck_pending_actions_kind"
        ),
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


class KnowledgeChunk(Base):
    """知识库里的一个小节：说明文档按“## 小节标题”切开后的一段原文。整个知识库重建时全部替换。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_search_vector", "search_vector", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # 来源标识：“文件名#小节标题”，文件名和小节标题不变，重建后也不变
    source_id: Mapped[str] = mapped_column(String(200), unique=True)
    document_title: Mapped[str] = mapped_column(String(100))
    section: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    # 文档标题、小节标题和原文分词后的词，用来做全文检索
    search_vector: Mapped[str] = mapped_column(TSVECTOR)
