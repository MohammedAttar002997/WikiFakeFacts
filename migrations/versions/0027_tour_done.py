"""tour done: when a player finished or skipped Finn's first-visit tour

Revision ID: 0027_tour_done
Revises: 0026_classrooms
Create Date: 2026-11-17 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0027_tour_done'
down_revision = '0026_classrooms'
branch_labels = None
depends_on = None


def upgrade():
    # A nullable column, so the code that's live while this is applied keeps working
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tour_done_at', sa.DateTime(), nullable=True))
    # Players who already know the game don't get the tour on its own; they can start it from the footer
    user = sa.table('user', sa.column('tour_done_at', sa.DateTime))
    op.execute(user.update().values(tour_done_at=sa.func.now()))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('tour_done_at')
