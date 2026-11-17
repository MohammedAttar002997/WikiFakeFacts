"""player fakes: fakes players write for rounds they played, and who played them

Revision ID: 0025_player_fakes
Revises: 0024_report_fingerprints
Create Date: 2026-11-17 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0025_player_fakes'
down_revision = '0024_report_fingerprints'
branch_labels = None
depends_on = None


def upgrade():
    # New tables only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'player_fake',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=100), nullable=False),
        sa.Column('real_facts_json', sa.Text(), nullable=False),
        sa.Column('fake', sa.String(length=300), nullable=False),
        sa.Column('correction', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=True),
        sa.Column('plays', sa.Integer(), nullable=False),
        sa.Column('fooled', sa.Integer(), nullable=False),
        sa.Column('reports', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index('ix_player_fake_user_id', 'player_fake', ['user_id'])
    op.create_index('ix_player_fake_created_at', 'player_fake', ['created_at'])
    op.create_table(
        'player_fake_play',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fake_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('picked', sa.String(length=300), nullable=False),
        sa.Column('correct', sa.Boolean(), nullable=False),
        sa.Column('reported', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['fake_id'], ['player_fake.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fake_id', 'user_id', name='uq_player_fake_play'),
    )
    op.create_index('ix_player_fake_play_user_id', 'player_fake_play', ['user_id'])


def downgrade():
    op.drop_index('ix_player_fake_play_user_id', table_name='player_fake_play')
    op.drop_table('player_fake_play')
    op.drop_index('ix_player_fake_created_at', table_name='player_fake')
    op.drop_index('ix_player_fake_user_id', table_name='player_fake')
    op.drop_table('player_fake')
