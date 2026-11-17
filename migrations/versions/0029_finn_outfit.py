"""finn outfit: what a player's Finn wears

Revision ID: 0029_finn_outfit
Revises: 0028_class_again
Create Date: 2026-11-17 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0029_finn_outfit'
down_revision = '0028_class_again'
branch_labels = None
depends_on = None


def upgrade():
    # A nullable column, so the code that's live while this is applied keeps working
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('finn_outfit', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('finn_outfit')
