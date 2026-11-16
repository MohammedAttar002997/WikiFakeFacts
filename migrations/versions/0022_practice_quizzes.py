"""practice quizzes: mark quiz sessions made of the player's own mistakes

Revision ID: 0022_practice_quizzes
Revises: 0021_push_subscriptions
Create Date: 2026-11-16 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0022_practice_quizzes'
down_revision = '0021_push_subscriptions'
branch_labels = None
depends_on = None


def upgrade():
    # A nullable column only (no value means a normal quiz), so the code that's live meanwhile keeps working
    with op.batch_alter_table('quiz_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('practice', sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table('quiz_session', schema=None) as batch_op:
        batch_op.drop_column('practice')
