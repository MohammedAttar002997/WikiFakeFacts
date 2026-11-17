"""follows: players following other players

Revision ID: 0031_follows
Revises: 0030_league_entries
Create Date: 2026-11-18 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0031_follows'
down_revision = '0030_league_entries'
branch_labels = None
depends_on = None


def upgrade():
    # A new table only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'follow',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('follower_id', sa.Integer(), nullable=False),
        sa.Column('followed_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['follower_id'], ['user.id']),
        sa.ForeignKeyConstraint(['followed_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('follower_id', 'followed_id', name='uq_follow'),
    )
    op.create_index('ix_follow_follower_id', 'follow', ['follower_id'])
    op.create_index('ix_follow_followed_id', 'follow', ['followed_id'])


def downgrade():
    op.drop_index('ix_follow_followed_id', table_name='follow')
    op.drop_index('ix_follow_follower_id', table_name='follow')
    op.drop_table('follow')
