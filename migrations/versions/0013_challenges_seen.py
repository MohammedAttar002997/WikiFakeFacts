"""challenges seen

Revision ID: 0013_challenges_seen
Revises: 0012_knowledge_chunks
Create Date: 2026-11-02 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0013_challenges_seen'
down_revision = '0012_knowledge_chunks'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('challenges_seen_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('challenges_seen_at')
