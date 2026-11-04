"""add order action

Revision ID: 6b1e4d2c9a07
Revises: 3c5a91d0b7e2
Create Date: 2026-11-04 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6b1e4d2c9a07'
down_revision: Union[str, Sequence[str], None] = '3c5a91d0b7e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# autogenerate 发现不了 CHECK 约束内容的变化，下面两条约束是手写的
OLD_KINDS = "kind IN ('return_request', 'return_cancellation', 'ticket')"
NEW_KINDS = "kind IN ('order', 'return_request', 'return_cancellation', 'ticket')"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('ck_pending_actions_kind', 'pending_actions', type_='check')
    op.create_check_constraint('ck_pending_actions_kind', 'pending_actions', NEW_KINDS)


def downgrade() -> None:
    """Downgrade schema."""
    refuse_if_data_would_be_lost()

    op.drop_constraint('ck_pending_actions_kind', 'pending_actions', type_='check')
    op.create_check_constraint('ck_pending_actions_kind', 'pending_actions', OLD_KINDS)


def refuse_if_data_would_be_lost() -> None:
    """已经有下单的待确认操作时，降级会让它们违反旧的约束，直接拒绝，不自动删改数据。"""
    order_actions = op.get_bind().scalar(sa.text("SELECT count(*) FROM pending_actions WHERE kind = 'order'"))
    if order_actions:
        raise RuntimeError(
            f"不能降级：有 {order_actions} 条下单的待确认操作，降级会让它们违反旧的约束。"
            "请先导出并人工处理这些数据；迁移不会自动删除用户数据。"
        )
