"""add knowledge embeddings

Revision ID: c4e8a1f2b7d3
Revises: 874f6907f7f9
Create Date: 2026-11-06 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'c4e8a1f2b7d3'
down_revision: Union[str, Sequence[str], None] = '874f6907f7f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column('knowledge_chunks', sa.Column('embedding', Vector(1024), nullable=True))
    op.create_table('knowledge_index',
    sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('embedding_signature', sa.String(length=200), nullable=True),
    sa.Column('built_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('id = 1', name='ck_knowledge_index_single_row'),
    sa.CheckConstraint("mode IN ('keyword', 'hybrid')", name='ck_knowledge_index_mode'),
    sa.CheckConstraint("(mode = 'hybrid') = (embedding_signature IS NOT NULL)", name='ck_knowledge_index_signature'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 向量和索引记录都能用 scripts.load_knowledge 重新生成，删除不会丢失用户数据。
    # vector 扩展保留：删扩展需要更高权限，留着也不影响旧版本
    op.drop_table('knowledge_index')
    op.drop_column('knowledge_chunks', 'embedding')
