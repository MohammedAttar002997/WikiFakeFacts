"""report actions: hide a reported round or mark the report as checked

Revision ID: 0020_report_actions
Revises: 0019_ready_quizzes
Create Date: 2026-11-16 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0020_report_actions'
down_revision = '0019_ready_quizzes'
branch_labels = None
depends_on = None


def upgrade():
    # A new table and nullable columns only (no status means open), so the code that's live meanwhile keeps working
    with op.batch_alter_table('round_report', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('handled_at', sa.DateTime(), nullable=True))
    op.create_table(
        'hidden_round',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fingerprint', sa.String(length=40), nullable=False),
        sa.Column('topic', sa.String(length=100), nullable=False),
        sa.Column('statements_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fingerprint'),
    )


def downgrade():
    op.drop_table('hidden_round')
    with op.batch_alter_table('round_report', schema=None) as batch_op:
        batch_op.drop_column('handled_at')
        batch_op.drop_column('status')
