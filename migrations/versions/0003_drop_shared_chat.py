"""delete the chatbot conversation every player used to share

Before conversations got a per-browser ID, every player's messages went into one row with
session_id 'default'. Nothing reads it anymore, and it mixes different players' messages.

Revision ID: 0003_drop_shared_chat
Revises: 0002_user_badges
Create Date: 2026-10-21 16:45:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0003_drop_shared_chat'
down_revision = '0002_user_badges'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM chatbot_session WHERE session_id = 'default'")


def downgrade():
    # The deleted conversation can't be restored
    pass
