"""live matches

Revision ID: 0017_live_matches
Revises: 0016_weekly_events
Create Date: 2026-11-12 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0017_live_matches'
down_revision = '0016_weekly_events'
branch_labels = None
depends_on = None


def upgrade():
    # A new table only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'live_match',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('guest_id', sa.Integer(), nullable=True),
        sa.Column('topic', sa.String(length=100), nullable=False),
        sa.Column('difficulty', sa.String(length=50), nullable=False),
        sa.Column('language', sa.String(length=50), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('questions_json', sa.Text(), nullable=False),
        sa.Column('explanations_json', sa.Text(), nullable=False),
        sa.Column('quotes_json', sa.Text(), nullable=True),
        sa.Column('state', sa.String(length=10), nullable=False),
        sa.Column('current_round', sa.Integer(), nullable=False),
        sa.Column('round_started_at', sa.DateTime(), nullable=True),
        sa.Column('answers_json', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('scores_saved', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['host_id'], ['user.id']),
        sa.ForeignKeyConstraint(['guest_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )


def downgrade():
    op.drop_table('live_match')
