"""report fingerprints: find every report of the same round without reading them all

Revision ID: 0024_report_fingerprints
Revises: 0023_cron_runs
Create Date: 2026-11-17 12:00:00.000000

"""
import hashlib
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0024_report_fingerprints'
down_revision = '0023_cron_runs'
branch_labels = None
depends_on = None


def fingerprint(statements):
    # A frozen copy of hidden_rounds.fingerprint, so this migration gives the same result even if that code changes
    text = json.dumps(sorted(statements), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:40]


def upgrade():
    # A nullable column, so the code that's live while this is applied keeps working; reports it makes meanwhile
    # get their fingerprint the first time the owner acts on a report
    with op.batch_alter_table('round_report', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fingerprint', sa.String(length=40), nullable=True))
        batch_op.create_index('ix_round_report_fingerprint', ['fingerprint'], unique=False)
    conn = op.get_bind()
    reports = sa.table('round_report', sa.column('id', sa.Integer), sa.column('statements_json', sa.Text),
                       sa.column('fingerprint', sa.String))
    for report_id, statements_json in conn.execute(sa.select(reports.c.id, reports.c.statements_json)).fetchall():
        conn.execute(reports.update().where(reports.c.id == report_id)
                     .values(fingerprint=fingerprint(json.loads(statements_json))))


def downgrade():
    with op.batch_alter_table('round_report', schema=None) as batch_op:
        batch_op.drop_index('ix_round_report_fingerprint')
        batch_op.drop_column('fingerprint')
