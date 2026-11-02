"""source quotes

Revision ID: 0015_source_quotes
Revises: 0014_rematch
Create Date: 2026-11-02 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0015_source_quotes'
down_revision = '0014_rematch'
branch_labels = None
depends_on = None

TABLES = ('quiz_session', 'daily_challenge')


def upgrade():
    # init_database() creates daily_challenge from the current models when a pre-migration database lacks it, so
    # there it already has these columns
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        existing = {column['name'] for column in inspector.get_columns(table)}
        with op.batch_alter_table(table, schema=None) as batch_op:
            if 'quotes_json' not in existing:
                batch_op.add_column(sa.Column('quotes_json', sa.Text(), nullable=True))
            if 'source_url' not in existing:
                batch_op.add_column(sa.Column('source_url', sa.String(length=500), nullable=True))


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('source_url')
            batch_op.drop_column('quotes_json')
