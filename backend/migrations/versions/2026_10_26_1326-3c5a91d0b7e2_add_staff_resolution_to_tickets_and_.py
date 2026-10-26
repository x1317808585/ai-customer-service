"""add staff resolution to tickets and returns

Revision ID: 3c5a91d0b7e2
Revises: f7d42726c26d
Create Date: 2026-10-26 03:09:30.014042

历史数据的处理方式：
- 工单此前都是 open，三项处理信息都为空，新约束直接校验全部数据。
- 退货申请里，此前已结束的记录没有处理人和原因，无法补造，所以不回填。
  两条新约束以 NOT VALID 添加：添加时不扫描历史数据，但以后新写入、或修改某一行时都要满足。
  历史已结束的退货申请是终态，业务代码不会再修改它们；如果以后需要修改，要先制定历史数据的兼容方案。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c5a91d0b7e2'
down_revision: Union[str, Sequence[str], None] = 'f7d42726c26d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TICKET_RESOLUTION = (
    "(status = 'open' AND resolution IS NULL AND resolved_by IS NULL AND resolved_at IS NULL)"
    " OR (status = 'closed' AND resolution IS NOT NULL AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tickets', sa.Column('resolution', sa.String(length=2000), nullable=True))
    op.add_column('tickets', sa.Column('resolved_by', sa.Integer(), nullable=True))
    op.add_column('tickets', sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_tickets_resolved_by', 'tickets', 'users', ['resolved_by'], ['id'])
    op.create_check_constraint('ck_tickets_resolution', 'tickets', TICKET_RESOLUTION)

    op.add_column('return_requests', sa.Column('resolved_by', sa.Integer(), nullable=True))
    op.add_column('return_requests', sa.Column('resolution_note', sa.String(length=500), nullable=True))
    op.create_foreign_key('fk_return_requests_resolved_by', 'return_requests', 'users', ['resolved_by'], ['id'])
    op.create_check_constraint(
        'ck_return_requests_resolved_by',
        'return_requests',
        "status = 'pending' OR resolved_by IS NOT NULL",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        'ck_return_requests_rejection_note',
        'return_requests',
        "status <> 'rejected' OR resolution_note IS NOT NULL",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    refuse_if_data_would_be_lost()

    op.drop_constraint('ck_return_requests_rejection_note', 'return_requests', type_='check')
    op.drop_constraint('ck_return_requests_resolved_by', 'return_requests', type_='check')
    op.drop_constraint('fk_return_requests_resolved_by', 'return_requests', type_='foreignkey')
    op.drop_column('return_requests', 'resolution_note')
    op.drop_column('return_requests', 'resolved_by')

    op.drop_constraint('ck_tickets_resolution', 'tickets', type_='check')
    op.drop_constraint('fk_tickets_resolved_by', 'tickets', type_='foreignkey')
    op.drop_column('tickets', 'resolved_at')
    op.drop_column('tickets', 'resolved_by')
    op.drop_column('tickets', 'resolution')


def refuse_if_data_would_be_lost() -> None:
    """已经有处理结果、处理人或原因时，降级会删掉这些列，直接拒绝，不自动删改数据。"""
    connection = op.get_bind()
    closed_tickets = connection.scalar(sa.text("SELECT count(*) FROM tickets WHERE status = 'closed'"))
    resolved_returns = connection.scalar(
        sa.text("SELECT count(*) FROM return_requests WHERE resolved_by IS NOT NULL OR resolution_note IS NOT NULL")
    )
    if closed_tickets or resolved_returns:
        raise RuntimeError(
            f"不能降级：有 {closed_tickets} 张已关闭的工单、{resolved_returns} 条记录了处理人或原因的退货申请。"
            "降级会删除这些处理记录。请先导出并人工处理这些数据；迁移不会自动删除用户数据。"
        )
