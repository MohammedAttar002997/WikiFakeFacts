"""class again: a room's settings and the room "play again" opened after it

Revision ID: 0028_class_again
Revises: 0027_tour_done
Create Date: 2026-11-17 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0028_class_again'
down_revision = '0027_tour_done'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable columns only, so the code that's live while this is applied keeps working
    with op.batch_alter_table('class_room', schema=None) as batch_op:
        batch_op.add_column(sa.Column('settings_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('next_code', sa.String(length=8), nullable=True))


def downgrade():
    with op.batch_alter_table('class_room', schema=None) as batch_op:
        batch_op.drop_column('next_code')
        batch_op.drop_column('settings_json')
