"""classrooms: a teacher's game that students join with a code and a nickname

Revision ID: 0026_classrooms
Revises: 0025_player_fakes
Create Date: 2026-11-17 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0026_classrooms'
down_revision = '0025_player_fakes'
branch_labels = None
depends_on = None


def upgrade():
    # New tables only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'class_room',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=8), nullable=False),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('topic', sa.String(length=100), nullable=False),
        sa.Column('difficulty', sa.String(length=50), nullable=False),
        sa.Column('language', sa.String(length=50), nullable=False),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('questions_json', sa.Text(), nullable=False),
        sa.Column('explanations_json', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=10), nullable=False),
        sa.Column('current_round', sa.Integer(), nullable=False),
        sa.Column('round_started_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['host_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index('ix_class_room_host_id', 'class_room', ['host_id'])
    op.create_index('ix_class_room_created_at', 'class_room', ['created_at'])
    op.create_table(
        'class_player',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('room_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=32), nullable=False),
        sa.Column('nickname', sa.String(length=20), nullable=False),
        sa.Column('answers_json', sa.Text(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['class_room.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('room_id', 'nickname', name='uq_class_player_nickname'),
    )
    op.create_index('ix_class_player_room_id', 'class_player', ['room_id'])


def downgrade():
    op.drop_index('ix_class_player_room_id', table_name='class_player')
    op.drop_table('class_player')
    op.drop_index('ix_class_room_created_at', table_name='class_room')
    op.drop_index('ix_class_room_host_id', table_name='class_room')
    op.drop_table('class_room')
