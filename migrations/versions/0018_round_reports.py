"""round reports

Revision ID: 0018_round_reports
Revises: 0017_live_matches
Create Date: 2026-11-12 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0018_round_reports'
down_revision = '0017_live_matches'
branch_labels = None
depends_on = None


def upgrade():
    # A new table only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'round_report',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('quiz_session_id', sa.Integer(), nullable=True),
        sa.Column('round_index', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=100), nullable=False),
        sa.Column('statements_json', sa.Text(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('comment', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['quiz_session_id'], ['quiz_session.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'quiz_session_id', 'round_index', name='uq_round_report'),
    )


def downgrade():
    op.drop_table('round_report')
