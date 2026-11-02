"""rematch

Revision ID: 0014_rematch
Revises: 0013_challenges_seen
Create Date: 2026-11-02 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0014_rematch'
down_revision = '0013_challenges_seen'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('quiz_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rematch_for_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_quiz_session_rematch_for_id', 'user', ['rematch_for_id'], ['id'])


def downgrade():
    with op.batch_alter_table('quiz_session', schema=None) as batch_op:
        batch_op.drop_constraint('fk_quiz_session_rematch_for_id', type_='foreignkey')
        batch_op.drop_column('rematch_for_id')
