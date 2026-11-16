"""What the timers call and what they feed: ready-made quizzes (/cron/refill, rules in ready_pool.py) and
daily-challenge reminders (/push/*, /cron/daily-reminder, rules in reminders.py). Routes register on the app when
app.py imports this."""
import hmac
import json
import random
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from flask import abort, jsonify, request, session, url_for
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import app as core
import ready_pool
import reminders
from app import app, current_user, daily_topic, db, describe_error, over_limit, save_error, today_utc
from default_text import TOPICS
from models import CronRun, DailyChallenge, PushSubscription, QuizDemand, QuizSession, ReadyQuiz
from translations import translate as _

# utcnow and get_ai_response are looked up on app (core) when called, so a stub set on app (as the tests do)
# applies here too


# ---------- Ready-made quizzes (rules in ready_pool.py) ------------------------------------------------------------

def note_demand(kind):
    """Remember that someone just asked for this kind of quiz, so the refill keeps some ready."""
    row = QuizDemand.query.filter_by(**kind._asdict()).first()
    if row:
        row.last_wanted = core.utcnow()
    else:
        db.session.add(QuizDemand(**kind._asdict(), last_wanted=core.utcnow()))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()  # Someone else asked for the same kind at the same moment; their row will do


def take_ready_quiz(kind, any_category=False):
    """Hand one ready quiz of this kind to the caller (of any category for "Surprise me"), or None if none is left.
    Two players asking at once can't get the same one: a quiz is only theirs if marking it as used succeeded."""
    query = ReadyQuiz.query.filter_by(difficulty=kind.difficulty, rounds=kind.rounds, language=kind.language,
                                      used_at=None)
    if any_category:
        query = query.order_by(func.random())
    else:
        query = query.filter_by(category=kind.category).order_by(ReadyQuiz.id)
    for candidate in query.limit(5).all():
        taken = ReadyQuiz.query.filter_by(id=candidate.id, used_at=None).update(
            {ReadyQuiz.used_at: core.utcnow()}, synchronize_session=False)
        db.session.commit()
        if taken:
            db.session.refresh(candidate)
            return candidate
    return None


def ready_stock():
    """{Kind: ready quizzes nobody has taken yet}."""
    rows = (db.session.query(ReadyQuiz.category, ReadyQuiz.difficulty, ReadyQuiz.rounds, ReadyQuiz.language,
                             func.count(ReadyQuiz.id))
            .filter(ReadyQuiz.used_at.is_(None))
            .group_by(ReadyQuiz.category, ReadyQuiz.difficulty, ReadyQuiz.rounds, ReadyQuiz.language).all())
    return {ready_pool.Kind(*row[:4]): row[4] for row in rows}


def made_ahead_since(moment):
    return ReadyQuiz.query.filter(ReadyQuiz.created_at >= moment).count()


def make_ready_quiz(kind):
    """Have the AI make one quiz of this kind and put it in stock."""
    topic = random.choice(TOPICS[kind.category])
    quiz = core.get_ai_response(kind.rounds, topic, kind.difficulty, kind.language)
    db.session.add(ReadyQuiz(**kind._asdict(), topic=topic[:100], source=quiz.source, source_url=quiz.source_url,
                             questions_json=json.dumps(quiz.rounds), explanations_json=json.dumps(quiz.explanations),
                             quotes_json=json.dumps(quiz.quotes)))
    db.session.commit()


def refill_ready_quizzes(clock=time.monotonic):
    """Make the quizzes ready_pool.refill_plan asks for, until RUN_SECONDS have passed. Returns a summary."""
    started, now = clock(), core.utcnow()
    day_ago = now - timedelta(days=1)
    # Taken quizzes are kept 2 days, long enough for the daily cap to count them, then forgotten
    ReadyQuiz.query.filter(ReadyQuiz.used_at < now - timedelta(days=2)).delete()
    db.session.commit()
    wanted = [(ready_pool.Kind(d.category, d.difficulty, d.rounds, d.language), d.last_wanted)
              for d in QuizDemand.query.filter(QuizDemand.last_wanted >= now - timedelta(days=ready_pool.WANTED_DAYS))
              if d.category in TOPICS]  # Skips a category that was renamed or removed since
    plan = ready_pool.refill_plan(wanted, ready_stock(), made_ahead_since(day_ago))
    made = 0
    for kind in plan:
        # Checked again before each quiz: the two timers may run at the same time
        if clock() - started > ready_pool.RUN_SECONDS or made_ahead_since(day_ago) >= ready_pool.DAILY_CAP:
            break
        try:
            make_ready_quiz(kind)
            made += 1
        except Exception as error:
            # Saved for /admin; stop here rather than pay for more calls that may fail the same way
            db.session.rollback()
            save_error(error)
            return {'made': made, 'planned': len(plan), 'error': describe_error(error)}
    return {'made': made, 'planned': len(plan), 'in_stock': sum(ready_stock().values())}


def record_cron_run(name, result):
    """Remember that timer `name` just ran (for the overdue warning on /admin) and pass its result on."""
    row = db.session.get(CronRun, name) or CronRun(name=name)
    row.last_run, row.last_result = core.utcnow(), json.dumps(result)[:300]
    db.session.add(row)
    db.session.commit()
    return result


def require_cron_secret():
    """401 unless the request carries CRON_SECRET as a Bearer token (Vercel's cron sends it by itself)."""
    secret = app.config.get('CRON_SECRET') or ''
    if not secret or not hmac.compare_digest(request.headers.get('Authorization', ''), f'Bearer {secret}'):
        abort(401)


@app.route('/cron/refill')
def cron_refill():
    """Called by the timers (vercel.json, .github/workflows/refill.yml)."""
    require_cron_secret()
    return jsonify(record_cron_run('refill', refill_ready_quizzes()))


# ---------- Daily-challenge reminders (rules in reminders.py) ------------------------------------------------------

def push_ready():
    return bool(app.config.get('VAPID_PUBLIC_KEY') and app.config.get('VAPID_PRIVATE_KEY'))


@app.route('/push/subscribe', methods=['POST'])
def push_subscribe():
    """Store the browser's push subscription for the signed-in player (JSON from PushSubscription.toJSON())."""
    user = current_user()
    if not user or not push_ready():
        abort(404)
    if over_limit('push', user.id):
        return jsonify(ok=False), 429
    data = request.get_json(silent=True) or {}
    endpoint, keys = data.get('endpoint'), data.get('keys') or {}
    p256dh, auth = keys.get('p256dh'), keys.get('auth')
    if (not reminders.valid_endpoint(endpoint) or not isinstance(p256dh, str) or not isinstance(auth, str)
            or not 0 < len(p256dh) <= 200 or not 0 < len(auth) <= 100):
        return jsonify(ok=False), 400
    row = PushSubscription.query.filter_by(endpoint=endpoint).first() or PushSubscription(endpoint=endpoint)
    # The same device may change hands: it belongs to whoever switched it on last
    row.user_id, row.p256dh, row.auth = user.id, p256dh, auth
    row.language = session.get('language', 'en')[:5]
    db.session.add(row)
    db.session.commit()
    extra = (PushSubscription.query.filter_by(user_id=user.id).order_by(PushSubscription.id.desc())
             .offset(reminders.MAX_PER_PLAYER).all())
    for old in extra:
        db.session.delete(old)
    db.session.commit()
    return jsonify(ok=True)


@app.route('/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    user = current_user()
    if not user:
        abort(404)
    endpoint = (request.get_json(silent=True) or {}).get('endpoint')
    PushSubscription.query.filter_by(user_id=user.id, endpoint=endpoint).delete()
    db.session.commit()
    return jsonify(ok=True)


def finished_daily_today(date):
    """Ids of players who finished the given day's challenge, in any language."""
    return {uid for (uid,) in db.session.query(QuizSession.user_id)
            .join(DailyChallenge, QuizSession.daily_challenge_id == DailyChallenge.id)
            .filter(DailyChallenge.date == date, QuizSession.is_completed.is_(True))}


# What a sending thread gets: plain values, since database rows mustn't be shared between threads
Device = namedtuple('Device', 'id endpoint p256dh auth language')


def send_push(subscription, message, vapid=None):
    """Send `message` (a dict) to one device (anything with endpoint, p256dh and auth). Returns the push service's
    error status, 0 when it couldn't be reached, or None if it worked. Safe to call from several threads at once."""
    try:
        webpush(subscription_info={'endpoint': subscription.endpoint,
                                   'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}},
                data=json.dumps(message), vapid_private_key=vapid or Vapid.from_string(app.config['VAPID_PRIVATE_KEY']),
                vapid_claims={'sub': app.config['VAPID_SUBJECT']}, ttl=6 * 3600, timeout=10)
        return None
    except WebPushException as error:
        return error.response.status_code if error.response is not None else 0
    except Exception:
        return 0  # Network trouble on the way to the push service: tried again on the next day's run


def send_daily_reminders(clock=time.monotonic):
    """Remind everyone due (reminders.due), SEND_WORKERS at a time, starting no new batch after RUN_SECONDS."""
    started, date = clock(), today_utc()
    topic = daily_topic(date)
    due = reminders.due(PushSubscription.query.all(), finished_daily_today(date), date)
    devices = [Device(s.id, s.endpoint, s.p256dh, s.auth, s.language) for s in due]
    # Made here, not in the threads: translations and url_for need this request's context
    messages = {lang: {'title': _("Today's challenge is ready", lang=lang),
                       'body': _("{topic}: three true facts and one fake. Can you spot it?", lang=lang).format(topic=topic),
                       'url': url_for('daily')} for lang in {d.language for d in devices}}
    vapid = Vapid.from_string(app.config['VAPID_PRIVATE_KEY']) if devices else None
    results = {}
    with ThreadPoolExecutor(max_workers=reminders.SEND_WORKERS) as pool:
        for batch in reminders.batches(devices):
            if clock() - started > reminders.RUN_SECONDS:
                break
            statuses = pool.map(lambda d: send_push(d, messages[d.language], vapid), batch)
            results.update(zip((d.id for d in batch), statuses))
    # Back in this thread: record what happened, in one commit
    sent = removed = failed = 0
    for subscription in due:
        if subscription.id not in results:
            continue
        status = results[subscription.id]
        if status is None:
            subscription.last_sent = date
            sent += 1
        elif reminders.gone(status):
            db.session.delete(subscription)
            removed += 1
        else:
            failed += 1
    db.session.commit()
    return {'sent': sent, 'removed': removed, 'failed': failed, 'left': len(devices) - len(results)}


@app.route('/cron/daily-reminder')
def cron_daily_reminder():
    """Called by Vercel's cron once a day, plus a catch-up call an hour later for anyone a slow run didn't reach
    (vercel.json); devices already reminded today are skipped."""
    require_cron_secret()
    if not push_ready():
        return jsonify(record_cron_run('daily-reminder', {'sent': 0, 'note': 'VAPID keys not set'}))
    return jsonify(record_cron_run('daily-reminder', send_daily_reminders()))
