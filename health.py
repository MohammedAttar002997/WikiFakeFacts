"""Two things that can break the live site without anyone noticing (pure rules, no database).

1. A migration that wasn't applied. Migrations don't run on Vercel: the owner runs `python migrate_production.py`
   from the pull request's branch before merging. If that's forgotten, the new code asks for tables or columns the
   database doesn't have and pages fail. /health reports both revisions, /admin shows a banner, and a pull-request
   check (.github/workflows/migration-applied.yml) fails until the database has the branch's migration.

2. A timer that stopped. /cron/refill and /cron/daily-reminder are called from outside (GitHub Actions, Vercel), and
   GitHub pauses scheduled workflows after 60 days without commits. Each call is recorded in `cron_run`; /admin warns
   when one is overdue.
"""
from datetime import timedelta

# How long after its last call a timer counts as stopped: the refill runs every 30 minutes, the reminder once a day
TIMER_LIMITS = {'refill': timedelta(hours=2), 'daily-reminder': timedelta(hours=26)}


def schema_state(database_revision, code_head, code_revisions):
    """'ok' when the database is at the code's newest migration; 'behind' when it's at an older one (pages that need
    the newer tables will fail); 'ahead' when it has a migration this code doesn't know yet (normal between running
    a pull request's migration and merging it); 'empty' when there's no migration at all."""
    if not database_revision:
        return 'empty'
    if database_revision == code_head:
        return 'ok'
    return 'behind' if database_revision in code_revisions else 'ahead'


def late_timers(now, last_runs, active, limits=TIMER_LIMITS):
    """[(name, last run or None)] of the active timers whose last call is longer ago than their limit (or that have
    never been called), in the order of `limits`."""
    return [(name, last_runs.get(name)) for name, limit in limits.items()
            if name in active and (last_runs.get(name) is None or now - last_runs[name] > limit)]


def migration_head(revisions):
    """The newest revision of a migration chain, from {revision: down_revision}; None if there isn't exactly one."""
    heads = set(revisions) - set(revisions.values())
    return heads.pop() if len(heads) == 1 else None
