"""Live head-to-head (/live/<code>); the rules are in live.py, this is the web side: load the match, move it on to
the current time, store it back only if nobody else did meanwhile. Routes register on the app when app.py imports
this."""
import json
import random
import secrets

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import update

import app as core
import live
from app import app, current_user, db, quiz_generation_allowed, quiz_settings_from_form, report_failure
from badges import award_badges
from models import LiveMatch, QuizSession, Score, User
from translations import translate as _

# utcnow and get_ai_response are looked up on app (core) when called, so a stub set on app (as the tests do)
# applies here too


# ---------- Live head-to-head (rules in live.py) --------------------------------------------------------------------
# Both players' pages ask /live/<code>/state every LIVE_POLL_MS; there are no websockets on Vercel's functions.
LIVE_POLL_MS = 1500


def live_state(row):
    """The match as live.py works on it."""
    return {'state': row.state, 'rounds': len(json.loads(row.questions_json)), 'current_round': row.current_round,
            'round_started_at': row.round_started_at, 'questions': json.loads(row.questions_json),
            'answers': json.loads(row.answers_json)}


def store_live(row, state, **extra):
    """Write the match back unless another request changed it since `row` was read. True if written."""
    written = db.session.execute(
        update(LiveMatch).where(LiveMatch.id == row.id, LiveMatch.version == row.version).values(
            state=state['state'], current_round=state['current_round'], round_started_at=state['round_started_at'],
            answers_json=json.dumps(state['answers']), version=row.version + 1, **extra)
    ).rowcount == 1
    db.session.commit()
    return written


def current_live(code, now):
    """(row, state) of the match moved on to `now`, or (None, None). Retries when another request wrote first."""
    for _attempt in range(3):
        row = LiveMatch.query.filter_by(code=code).first()
        if not row:
            return None, None
        db.session.refresh(row)
        state = live_state(row)
        if not live.advance(state, now, created_at=row.created_at) or store_live(row, state):
            db.session.refresh(row)
            return row, state
    return row, live_state(row)


def live_role(row, user):
    if user and user.id == row.host_id:
        return 'host'
    if user and user.id == row.guest_id:
        return 'guest'
    return None


def save_live_scores(row, state):
    """Once a match ends, add it to both players' games, like any finished quiz (history, stats, leaderboard,
    badges). Only the first request to get here does it."""
    claimed = db.session.execute(update(LiveMatch).where(LiveMatch.id == row.id, LiveMatch.scores_saved.is_(False))
                                 .values(scores_saved=True)).rowcount == 1
    db.session.commit()
    if not claimed:
        return
    rounds = state['rounds']
    for role, user_id in (('host', row.host_id), ('guest', row.guest_id)):
        entries = state['answers'][role]
        correct = sum(1 for e in entries if e and e['correct'])
        quiz = QuizSession(user_id=user_id, topic=row.topic, difficulty=row.difficulty,
                           questions_json=row.questions_json, explanations_json=row.explanations_json,
                           quotes_json=row.quotes_json, source_url=row.source_url,
                           answers_json=json.dumps([e['pick'] if e else None for e in entries]),
                           total_questions=rounds, current_question_index=rounds, score=correct, is_completed=True)
        db.session.add(quiz)
        db.session.flush()
        db.session.add(Score(user_id=user_id, topic=row.topic, difficulty=row.difficulty, score=correct,
                             total_questions=rounds, percentage=correct / rounds * 100, quiz_session_id=quiz.id))
    db.session.commit()
    from views.leagues import join_league  # Imported here: views.leagues is loaded after this module
    for user_id in (row.host_id, row.guest_id):
        player = db.session.get(User, user_id)
        join_league(player)
        award_badges(player)


def live_payload(row, state, role, now):
    """What a player's page needs to show the match right now."""
    phase, seconds = live.phase(state, now)
    totals = live.visible_totals(state, now)
    names = {'host': db.session.get(User, row.host_id).username,
             'guest': db.session.get(User, row.guest_id).username if row.guest_id else None}
    index = state['current_round']
    payload = {'phase': phase, 'seconds': seconds, 'round': index + 1, 'rounds': state['rounds'], 'you': role,
               'players': {r: {'name': names[r], 'points': totals[r]['points'], 'correct': totals[r]['correct'],
                               'answered': bool(live.answer_of(state, r, index))} for r in live.ROLES}}
    explanations = json.loads(row.explanations_json or '[]')

    def round_result(i):
        facts = state['questions'][i]
        return {'fake': next(s for s, real in facts.items() if real is False),
                'explanation': explanations[i] if i < len(explanations) else None,
                'answers': {r: live.answer_of(state, r, i) for r in live.ROLES}}

    if phase in ('question', 'reveal'):
        # Same order for both players, different every round
        payload['options'] = random.Random(f'{row.code}:{index}').sample(list(state['questions'][index]), 4)
        mine = live.answer_of(state, role, index) if role else None
        payload['your_pick'] = mine['pick'] if mine else None
    if phase == 'reveal':
        payload['result'] = round_result(index)
    if phase == 'finished':
        payload['winner'] = live.winner(state)
        payload['summary'] = [round_result(i) for i in range(state['rounds'])]
    return payload


@app.route('/live/new', methods=['POST'])
def live_new():
    """Make a live match from the setup page's choices and show its invite link."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    settings = quiz_settings_from_form()
    if not settings or not quiz_generation_allowed(user):
        return redirect(url_for('setup'))
    topic, difficulty, rounds, language_name, _category, _surprise = settings
    try:
        quiz = core.get_ai_response(rounds, topic, difficulty, language_name)
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('setup'))
    row = LiveMatch(code=secrets.token_urlsafe(9), host_id=user.id, topic=topic[:100], difficulty=difficulty,
                    language=language_name, source=quiz.source, source_url=quiz.source_url,
                    questions_json=json.dumps(quiz.rounds), explanations_json=json.dumps(quiz.explanations),
                    quotes_json=json.dumps(quiz.quotes), state='waiting', current_round=0,
                    answers_json=json.dumps({'host': [], 'guest': []}), version=0, scores_saved=False,
                    created_at=core.utcnow())
    db.session.add(row)
    db.session.commit()
    return redirect(url_for('live_match', code=row.code))


@app.route('/live/<code>')
def live_match(code):
    row, state = current_live(code, core.utcnow())
    if not row:
        abort(404)
    user = current_user()
    if not user:
        # Back here after signing in
        session['next_url'] = url_for('live_match', code=code)
        flash(_("Sign in to play this live match."), "info")
        return redirect(url_for('index'))
    role = live_role(row, user)
    can_join = role is None and row.guest_id is None and state['state'] == 'waiting'
    if role is None and not can_join:
        flash(_("This match is already full."), "warning")
        return redirect(url_for('setup'))
    return render_template('live.html', match=row, role=role, can_join=can_join, poll_ms=LIVE_POLL_MS,
                           host_name=db.session.get(User, row.host_id).username,
                           invite_url=url_for('live_match', code=code, _external=True))


@app.route('/live/<code>/join', methods=['POST'])
def live_join(code):
    user = current_user()
    if not user:
        return redirect(url_for('live_match', code=code))
    now = core.utcnow()
    row, state = current_live(code, now)
    if not row:
        abort(404)
    if live_role(row, user) is None and state['state'] == 'waiting':
        live.start(state, now)
        # Only if nobody joined in the meantime
        db.session.execute(update(LiveMatch).where(
            LiveMatch.id == row.id, LiveMatch.guest_id.is_(None), LiveMatch.state == 'waiting',
            LiveMatch.host_id != user.id).values(
            guest_id=user.id, state=state['state'], current_round=0, round_started_at=state['round_started_at'],
            version=row.version + 1))
        db.session.commit()
    return redirect(url_for('live_match', code=code))


@app.route('/live/<code>/state')
def live_status(code):
    now = core.utcnow()
    row, state = current_live(code, now)
    if not row:
        return jsonify(error='not_found'), 404
    role = live_role(row, current_user())
    if role is None and not (row.guest_id is None and state['state'] == 'waiting'):
        return jsonify(error='forbidden'), 403
    if state['state'] == 'finished' and not row.scores_saved:
        save_live_scores(row, state)
    return jsonify(live_payload(row, state, role, now))


@app.route('/live/<code>/answer', methods=['POST'])
def live_answer(code):
    data = request.get_json(silent=True) or {}
    round_number = data.get('round')
    if not isinstance(round_number, int) or not isinstance(data.get('option'), str):
        return jsonify(ok=False), 400
    user = current_user()
    for _attempt in range(3):
        now = core.utcnow()
        row, state = current_live(code, now)
        role = live_role(row, user) if row else None
        if not role:
            return jsonify(ok=False), 403
        entry = live.record_answer(state, role, round_number - 1, data['option'], now)
        if not entry:
            return jsonify(ok=False)
        if store_live(row, state):
            return jsonify(ok=True)
    return jsonify(ok=False)
