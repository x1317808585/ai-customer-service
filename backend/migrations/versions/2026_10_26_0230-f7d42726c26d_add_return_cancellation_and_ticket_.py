"""add return cancellation and ticket actions

Revision ID: f7d42726c26d
Revises: 8e8cacb502b2
Create Date: 2026-10-26 01:55:50.353845

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7d42726c26d'
down_revision: Union[str, Sequence[str], None] = '8e8cacb502b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# autogenerate 发现不了 CHECK 约束内容的变化，下面两条约束是手写的
OLD_KINDS = "kind IN ('return_request')"
NEW_KINDS = "kind IN ('return_request', 'return_cancellation', 'ticket')"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('ck_pending_actions_kind', 'pending_actions', type_='check')
    op.create_check_constraint('ck_pending_actions_kind', 'pending_actions', NEW_KINDS)

    op.add_column('tickets', sa.Column('conversation_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_tickets_conversation_id'), 'tickets', ['conversation_id'], unique=False)
    op.create_foreign_key(
        'fk_tickets_conversation_id', 'tickets', 'conversations', ['conversation_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    """Downgrade schema."""
    refuse_if_data_would_be_lost()

    op.drop_constraint('fk_tickets_conversation_id', 'tickets', type_='foreignkey')
    op.drop_index(op.f('ix_tickets_conversation_id'), table_name='tickets')
    op.drop_column('tickets', 'conversation_id')

    op.drop_constraint('ck_pending_actions_kind', 'pending_actions', type_='check')
    op.create_check_constraint('ck_pending_actions_kind', 'pending_actions', OLD_KINDS)


def refuse_if_data_would_be_lost() -> None:
    """已经有新类型的操作、或记录了来源会话的工单时，降级会破坏或丢掉它们，直接拒绝，不自动删改数据。"""
    connection = op.get_bind()
    new_kind_actions = connection.scalar(
        sa.text("SELECT count(*) FROM pending_actions WHERE kind IN ('return_cancellation', 'ticket')")
    )
    linked_tickets = connection.scalar(sa.text("SELECT count(*) FROM tickets WHERE conversation_id IS NOT NULL"))
    if new_kind_actions or linked_tickets:
        raise RuntimeError(
            f"不能降级：有 {new_kind_actions} 条撤回退货或转人工的待确认操作、"
            f"{linked_tickets} 张记录了来源会话的工单。降级会让它们违反旧的约束或丢失来源会话。"
            "请先导出并人工处理这些数据；迁移不会自动删除用户数据。"
        )
