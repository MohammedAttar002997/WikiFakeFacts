"""Warnings for what breaks the site quietly: a migration not applied, a timer that stopped (health.py)."""
import io
import json
import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

import app as app_module
from app import db
from health import late_timers, migration_head, schema_state
from models import CronRun

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import check_migration_applied  # noqa: E402

NOW = datetime(2026, 11, 17, 12, 0)
CHAIN = {'0001': None, '0002': '0001', '0003': '0002'}


# ---------- Rules -------------------------------------------------------------------------------------------------

def test_migration_head_is_the_one_nothing_follows():
    assert migration_head(CHAIN) == '0003'
    assert migration_head({**CHAIN, '0004a': '0003', '0004b': '0003'}) is None  # Two heads: ambiguous


@pytest.mark.parametrize('database, state', [('0003', 'ok'), ('0002', 'behind'), ('0004', 'ahead'), (None, 'empty')])
def test_schema_state(database, state):
    assert schema_state(database, '0003', set(CHAIN)) == state


def test_late_timers():
    runs = {'refill': NOW - timedelta(hours=3), 'daily-reminder': NOW - timedelta(hours=20)}
    assert late_timers(NOW, runs, {'refill', 'daily-reminder'}) == [('refill', runs['refill'])]
    assert late_timers(NOW, {}, {'refill'}) == [('refill', None)]
    assert late_timers(NOW, {}, set()) == []  # Timers that aren't set up aren't expected to run


# ---------- In the app --------------------------------------------------------------------------------------------

@pytest.fixture
def admins(app, monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_USERS', 'Boss')


@pytest.fixture
def database_at(app_ctx):
    """Pretend the database is at another migration for one test."""
    with db.engine.begin() as conn:
        real = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()

    def set_to(revision):
        with db.engine.begin() as conn:
            conn.execute(text('UPDATE alembic_version SET version_num = :r'), {'r': revision})
    yield set_to
    set_to(real)


def test_health_reports_both_revisions(app):
    data = app.test_client().get('/health').get_json()
    assert data['database'] == data['code'] and data['schema'] == 'ok'


def test_admin_warns_when_a_migration_is_missing(admins, login, database_at):
    assert 'missing a migration' not in login('Boss').get('/admin').get_data(as_text=True)
    database_at('0021_push_subscriptions')
    html = login('Boss').get('/admin').get_data(as_text=True)
    assert 'missing a migration' in html and '0021_push_subscriptions' in html


def test_a_newer_database_is_fine(admins, login, database_at, app):
    database_at('0099_from_an_open_pull_request')
    assert app.test_client().get('/health').get_json()['schema'] == 'ahead'
    assert 'missing a migration' not in login('Boss').get('/admin').get_data(as_text=True)


def test_timers_are_recorded_and_late_ones_flagged(admins, login, app, monkeypatch, app_ctx):
    monkeypatch.setitem(app.config, 'CRON_SECRET', 'secret')
    assert 'refill timer seems to have stopped' in login('Boss').get('/admin').get_data(as_text=True)
    app.test_client().get('/cron/refill', headers={'Authorization': 'Bearer secret'})
    assert db.session.get(CronRun, 'refill').last_result.startswith('{')
    assert 'timer seems to have stopped' not in login('Boss').get('/admin').get_data(as_text=True)
    db.session.get(CronRun, 'refill').last_run = app_module.utcnow() - timedelta(hours=5)
    db.session.commit()
    assert 'refill timer seems to have stopped' in login('Boss').get('/admin').get_data(as_text=True)


def test_without_a_cron_secret_no_timer_is_expected(admins, login, app, monkeypatch):
    monkeypatch.setitem(app.config, 'CRON_SECRET', '')
    assert 'timer seems to have stopped' not in login('Boss').get('/admin').get_data(as_text=True)


# ---------- The pull-request check (scripts/check_migration_applied.py) -------------------------------------------

def run_check(monkeypatch, database):
    body = json.dumps({'database': database}).encode()
    monkeypatch.setattr(check_migration_applied.urllib.request, 'urlopen', lambda url, timeout: io.BytesIO(body))
    return check_migration_applied.main('https://example.test/health')


def test_the_check_passes_once_the_branch_migration_is_applied(monkeypatch):
    head = migration_head(check_migration_applied.branch_revisions())
    assert run_check(monkeypatch, head) == 0
    assert run_check(monkeypatch, '0999_newer_elsewhere') == 0


def test_the_check_only_warns_while_health_isnt_live_yet(monkeypatch, capsys):
    def not_found(url, timeout):
        raise check_migration_applied.urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
    monkeypatch.setattr(check_migration_applied.urllib.request, 'urlopen', not_found)
    assert check_migration_applied.main('https://example.test/health') == 0
    assert 'not live yet' in capsys.readouterr().out


def test_the_check_fails_while_the_database_is_behind(monkeypatch, capsys):
    assert run_check(monkeypatch, '0001_baseline') == 1
    assert 'migrate_production.py' in capsys.readouterr().out
