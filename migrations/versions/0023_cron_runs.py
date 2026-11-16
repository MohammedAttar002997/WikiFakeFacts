"""cron runs: when each timer last called the site

Revision ID: 0023_cron_runs
Revises: 0022_practice_quizzes
Create Date: 2026-11-17 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0023_cron_runs'
down_revision = '0022_practice_quizzes'
branch_labels = None
depends_on = None


def upgrade():
    # A new table only, so the code that's live while this is applied doesn't notice
    op.create_table(
        'cron_run',
        sa.Column('name', sa.String(length=30), nullable=False),
        sa.Column('last_run', sa.DateTime(), nullable=False),
        sa.Column('last_result', sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade():
    op.drop_table('cron_run')
