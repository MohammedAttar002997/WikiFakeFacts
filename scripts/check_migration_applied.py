"""Fail unless the database behind a /health URL is at this checkout's newest migration (or a newer one).

    python scripts/check_migration_applied.py https://www.wikifakefacts.com/health

Used by .github/workflows/migration-applied.yml. Needs only the standard library: the migration files are read as
text, not imported, so the check doesn't have to install the app.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from health import migration_head  # noqa: E402  (pure module, standard library only)

VERSIONS = os.path.join(ROOT, 'migrations', 'versions')


def branch_revisions():
    """{revision: down_revision} of every migration file in this checkout."""
    revisions = {}
    for name in sorted(os.listdir(VERSIONS)):
        if not name.endswith('.py'):
            continue
        source = open(os.path.join(VERSIONS, name), encoding='utf-8').read()
        revision = re.search(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", source, re.M)
        down = re.search(r"^down_revision\s*=\s*(?:['\"]([^'\"]+)['\"]|None)", source, re.M)
        if revision:
            revisions[revision.group(1)] = down.group(1) if down else None
    return revisions


def main(url):
    revisions = branch_revisions()
    head = migration_head(revisions)
    if not head:
        print('Could not find a single newest migration in migrations/versions.')
        return 1
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            database = json.load(response).get('database')
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        # Only before the first deploy that has /health; afterwards a 404 can't happen
        print(f'::warning::{url} is not live yet, so this check cannot tell. Run '
              '`python migrate_production.py` from this branch before merging.')
        return 0
    print(f'This branch needs {head}; the production database is at {database}.')
    if database == head or (database and database not in revisions):
        # Equal, or newer than anything here (another branch's migration, already applied): nothing missing
        print('The migration is applied. Safe to merge.')
        return 0
    print('::error::The production database does not have this branch\'s migration yet. Run '
          '`python migrate_production.py` from this branch, then re-run this check.')
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
