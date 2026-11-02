"""weekly events

Revision ID: 0016_weekly_events
Revises: 0015_source_quotes
Create Date: 2026-11-02 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0016_weekly_events'
down_revision = '0015_source_quotes'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('quiz_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('event_week', sa.String(length=8), nullable=True))
        batch_op.create_index(batch_op.f('ix_quiz_session_event_week'), ['event_week'], unique=False)


def downgrade():
    with op.batch_alter_table('quiz_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quiz_session_event_week'))
        batch_op.drop_column('event_week')
