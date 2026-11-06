"""create knowledge documents

Revision ID: d7f3b2a9c5e1
Revises: c4e8a1f2b7d3
Create Date: 2026-11-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f3b2a9c5e1'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1f2b7d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 原来的段落和 knowledge_index 都是从 backend/knowledge 生成的派生数据：
    # 升级后运行 uv run python -m scripts.load_knowledge 重新导入
    op.execute("DELETE FROM knowledge_chunks")
    op.drop_table('knowledge_index')
    op.create_table('knowledge_documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('markdown', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('index_job', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('indexed_version', sa.Integer(), nullable=True),
    sa.Column('embedding_signature', sa.String(length=200), nullable=True),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'processing', 'ready', 'failed')", name='ck_knowledge_documents_status'),
    sa.CheckConstraint("(status = 'ready') = (indexed_version IS NOT NULL)", name='ck_knowledge_documents_indexed_version'),
    sa.CheckConstraint("(status = 'failed') = (error IS NOT NULL)", name='ck_knowledge_documents_error'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.add_column('knowledge_chunks', sa.Column('document_id', sa.Integer(), nullable=False))
    op.add_column('knowledge_chunks', sa.Column('position', sa.Integer(), nullable=False))
    op.create_foreign_key(None, 'knowledge_chunks', 'knowledge_documents', ['document_id'], ['id'], ondelete='CASCADE')
    op.create_unique_constraint('uq_knowledge_chunks_document_position', 'knowledge_chunks', ['document_id', 'position'])


def downgrade() -> None:
    """Downgrade schema."""
    refuse_if_data_would_be_lost()

    # 删列时外键和唯一约束会一起删掉
    op.drop_column('knowledge_chunks', 'position')
    op.drop_column('knowledge_chunks', 'document_id')
    op.drop_table('knowledge_documents')
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


def refuse_if_data_would_be_lost() -> None:
    """数据库是知识文档的来源，管理员上传的原文只存在这里。有文档时降级会删掉它们，直接拒绝。"""
    saved = op.get_bind().scalar(sa.text("SELECT count(*) FROM knowledge_documents"))
    if saved:
        raise RuntimeError(
            f"不能降级：已经保存了 {saved} 篇知识文档，降级会删掉它们（管理员上传的原文只存在数据库里）。"
            "请先导出并人工处理这些数据；迁移不会自动删除用户数据。"
        )
