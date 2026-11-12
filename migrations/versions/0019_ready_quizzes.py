"""ready quizzes

Revision ID: 0019_ready_quizzes
Revises: 0018_round_reports
Create Date: 2026-11-12 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0019_ready_quizzes'
down_revision = '0018_round_reports'
branch_labels = None
depends_on = None


def upgrade():
    # New tables only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'ready_quiz',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('difficulty', sa.String(length=50), nullable=False),
        sa.Column('rounds', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=50), nullable=False),
        sa.Column('topic', sa.String(length=100), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('questions_json', sa.Text(), nullable=False),
        sa.Column('explanations_json', sa.Text(), nullable=False),
        sa.Column('quotes_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('quiz_session_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['quiz_session_id'], ['quiz_session.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ready_quiz_kind', 'ready_quiz', ['category', 'difficulty', 'rounds', 'language', 'used_at'])
    op.create_index('ix_ready_quiz_created_at', 'ready_quiz', ['created_at'])
    op.create_table(
        'quiz_demand',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('difficulty', sa.String(length=50), nullable=False),
        sa.Column('rounds', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=50), nullable=False),
        sa.Column('last_wanted', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('category', 'difficulty', 'rounds', 'language', name='uq_quiz_demand'),
    )


def downgrade():
    op.drop_table('quiz_demand')
    op.drop_index('ix_ready_quiz_created_at', table_name='ready_quiz')
    op.drop_index('ix_ready_quiz_kind', table_name='ready_quiz')
    op.drop_table('ready_quiz')
