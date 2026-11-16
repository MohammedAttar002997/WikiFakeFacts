"""The owner's pages and commands: /admin (numbers in admin_stats.py), acting on reports, /health (rules in
health.py) and the `flask` commands for reports and players. Routes register on the app when app.py imports this."""
import json
import secrets

import click
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import abort, jsonify, redirect, render_template, url_for
from sqlalchemy import func
from werkzeug.security import generate_password_hash

import app as core
import health
import hidden_rounds
from admin_stats import dashboard as admin_dashboard
from app import app, current_user, db, delete_player_data, migrate, print_errors, save_error
from models import CronRun, HiddenRound, RoundReport, User
from views.timers import push_ready

# utcnow and get_ai_response are looked up on app (core) when called, so a stub set on app (as the tests do)
# applies here too


def is_admin(user):
    """Whether `user` is one of the ADMIN_USERS (names compared case-insensitively)."""
    names = {n.strip().casefold() for n in (app.config.get('ADMIN_USERS') or '').split(',') if n.strip()}
    return bool(user) and user.username.casefold() in names


_code_migrations = None


def code_migrations():
    """({revision: down_revision} of the migrations shipped with this code, its newest revision), read once."""
    global _code_migrations
    if _code_migrations is None:
        scripts = ScriptDirectory.from_config(migrate.get_config())
        revisions = {s.revision: s.down_revision for s in scripts.walk_revisions()}
        _code_migrations = (revisions, health.migration_head(revisions))
    return _code_migrations


def site_health():
    """What /health reports and /admin warns about (rules in health.py)."""
    revisions, head = code_migrations()
    with db.engine.connect() as conn:
        database = MigrationContext.configure(conn).get_current_revision()
    active = {'refill'} if app.config.get('CRON_SECRET') else set()
    if active and push_ready():
        active.add('daily-reminder')
    try:
        last_runs = {r.name: r.last_run for r in CronRun.query}
    except Exception:
        db.session.rollback()  # No cron_run table yet: that's a missing migration, which the banner already says
        last_runs = {}
    return {'database': database, 'code': head, 'schema': health.schema_state(database, head, set(revisions)),
            'late_timers': health.late_timers(core.utcnow(), last_runs, active)}


@app.route('/health')
def health_check():
    """Which migration the database and the code are at. Public on purpose: revision names reveal nothing, and the
    pull-request check (.github/workflows/migration-applied.yml) reads it to see whether a migration was applied."""
    status = site_health()
    response = jsonify(database=status['database'], code=status['code'], schema=status['schema'])
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/admin')
def admin():
    """Numbers for the site's owner. Anyone else, signed in or not, gets a plain 404, as if it didn't exist."""
    user = current_user()
    if not is_admin(user):
        abort(404)
    status = site_health()
    try:
        stats = admin_dashboard(core.utcnow())
    except Exception as error:
        # With a migration missing, the numbers can fail too; the page still shows what's wrong
        save_error(error)
        if status['schema'] != 'behind':
            raise
        return render_template('admin.html', stats=None, peak=1, health=status)
    peak = max([n for _, n in stats['per_day']] + [1])
    return render_template('admin.html', stats=stats, peak=peak, health=status)


REPORT_ACTIONS = {'hide': 'hidden', 'check': 'checked', 'reopen': None}


@app.route('/admin/reports/<int:report_id>/<action>', methods=['POST'])
def admin_report_action(report_id, action):
    """Hide a reported round for good, mark its report as checked, or undo either. Every report of the same round
    (several players may report it) changes with it."""
    report = db.session.get(RoundReport, report_id)
    if not is_admin(current_user()) or not report or action not in REPORT_ACTIONS:
        abort(404)
    statements = json.loads(report.statements_json)
    fingerprint = hidden_rounds.fingerprint(statements)
    status = REPORT_ACTIONS[action]
    for same in RoundReport.query.all():
        if hidden_rounds.fingerprint(json.loads(same.statements_json)) == fingerprint:
            same.status, same.handled_at = status, core.utcnow() if status else None
    hidden = HiddenRound.query.filter_by(fingerprint=fingerprint).first()
    if status == 'hidden' and not hidden:
        db.session.add(HiddenRound(fingerprint=fingerprint, topic=report.topic, statements_json=report.statements_json))
    elif status != 'hidden' and hidden:
        db.session.delete(hidden)
    db.session.commit()
    return redirect(url_for('admin', _anchor='reports'))


@app.cli.command('reports')
@click.option('-n', '--limit', default=20, show_default=True, help='How many to show.')
def reports_command(limit):
    """Show the newest rounds players reported as wrong."""
    reports = RoundReport.query.order_by(RoundReport.id.desc()).limit(limit).all()
    if not reports:
        click.echo('No reports.')
    for r in reports:
        user = db.session.get(User, r.user_id)
        facts = json.loads(r.statements_json)
        fake = next((s for s, real in facts.items() if real is False), '?')
        click.echo(f'{r.created_at:%Y-%m-%d %H:%M} UTC  {user.username if user else "?"}  [{r.reason}]  {r.topic}'
                   f'  ({r.status or "open"})')
        click.echo(f'    Fake: {fake}')
        for statement, real in facts.items():
            if real:
                click.echo(f'    True: {statement}')
        if r.explanation:
            click.echo(f'    Why:  {r.explanation}')
        if r.comment:
            click.echo(f'    Comment: {r.comment}')
        if r.source_url:
            click.echo(f'    Source: {r.source_url}')


@app.cli.command('errors')
@click.option('-n', '--limit', default=20, show_default=True, help='How many to show.')
@click.option('--full', is_flag=True, help='Also show where in the code each one happened.')
def errors_command(limit, full):
    """Show the newest unexpected errors."""
    print_errors(limit, full)


@app.cli.command('reset-pin')
@click.argument('username')
@click.option('--pin', help='New 4-digit PIN (random if omitted).')
def reset_pin_command(username, pin):
    """Set a new PIN for a player who forgot theirs, and print it."""
    user = User.query.filter(func.lower(User.username) == username.strip().lower()).first()
    if not user:
        raise click.ClickException(f'No player called "{username}".')
    if pin is None:
        pin = f'{secrets.randbelow(10000):04d}'
    elif not (len(pin) == 4 and pin.isdigit()):
        raise click.ClickException('The PIN must be 4 digits.')
    user.pin_hash = generate_password_hash(pin)
    db.session.commit()
    click.echo(f'New PIN for {user.username}: {pin}')


@app.cli.command('delete-player')
@click.argument('username')
@click.option('--yes', is_flag=True, help="Don't ask for confirmation.")
def delete_player_command(username, yes):
    """Delete a player and all their data."""
    user = User.query.filter(func.lower(User.username) == username.strip().lower()).first()
    if not user:
        raise click.ClickException(f'No player called "{username}".')
    if not yes:
        click.confirm(f'Delete {user.username} and all their scores, quizzes, uploads and badges?', abort=True)
    delete_player_data(user)
    click.echo(f'Deleted {username}.')
