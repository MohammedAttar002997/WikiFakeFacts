"""league entries: which league and group a player is in each week

Revision ID: 0030_league_entries
Revises: 0029_finn_outfit
Create Date: 2026-11-18 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0030_league_entries'
down_revision = '0029_finn_outfit'
branch_labels = None
depends_on = None


def upgrade():
    # A new table only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'league_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('week', sa.String(length=8), nullable=False),
        sa.Column('tier', sa.Integer(), nullable=False),
        sa.Column('group_no', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'week', name='uq_league_entry_user_week'),
    )
    op.create_index('ix_league_entry_group', 'league_entry', ['week', 'tier', 'group_no'])


def downgrade():
    op.drop_index('ix_league_entry_group', table_name='league_entry')
    op.drop_table('league_entry')
