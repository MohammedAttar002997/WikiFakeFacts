"""Classroom mode (rules in classroom.py): the teacher's board (/class/<code>/board), students joining with a code
and a nickname (/join, /class/<code>), and the JSON both pages poll (/class/<code>/state). Routes register on the app
when app.py imports this."""
import json
import random
import secrets
from datetime import timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

import app as core
import classroom as rules
import ready_pool
from app import (app, current_user, db, quiz_generation_allowed, quiz_settings_from_form, report_failure)
from models import ClassPlayer, ClassRoom
from translations import translate as _
from views.timers import take_ready_quiz

SEATS = 'class_seats'  # Session key: {room code: this student's token}
POLL_MS = 1500


def room_or_404(code):
    room = ClassRoom.query.filter_by(code=rules.normalize_code(code)).first()
    if not room or rules.expired(room.created_at, core.utcnow()):
        abort(404)
    return room


def require_teacher(room):
    user = current_user()
    if not user or user.id != room.host_id:
        abort(404)
    return user


def my_seat(room):
    """This visitor's ClassPlayer in the room, or None."""
    token = session.get(SEATS, {}).get(room.code)
    return ClassPlayer.query.filter_by(room_id=room.id, token=token).first() if token else None


def forget_old_rooms(now):
    """Rooms (and every nickname in them) go ROOM_HOURS after they were opened."""
    old = [r.id for r in ClassRoom.query.filter(ClassRoom.created_at < now - timedelta(hours=rules.ROOM_HOURS))
           .with_entities(ClassRoom.id)]
    if old:
        # 'fetch' also drops them from this session, so a new room that gets a reused id isn't mistaken for one
        ClassPlayer.query.filter(ClassPlayer.room_id.in_(old)).delete(synchronize_session='fetch')
        ClassRoom.query.filter(ClassRoom.id.in_(old)).delete(synchronize_session='fetch')
        db.session.commit()


@app.route('/class/new', methods=['POST'])
def class_new():
    """Open a room with the setup page's choices (a ready-made quiz when there is one, else a fresh one)."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    settings = quiz_settings_from_form()
    if not settings or not quiz_generation_allowed(user):
        return redirect(url_for('setup'))
    topic, difficulty, rounds, language_name, category, surprise = settings
    now = core.utcnow()
    forget_old_rooms(now)
    ready = take_ready_quiz(ready_pool.Kind(category, difficulty, rounds, language_name), any_category=surprise)
    if ready:
        topic, questions, explanations, source_url = (ready.topic, json.loads(ready.questions_json),
                                                      json.loads(ready.explanations_json), ready.source_url)
    else:
        try:
            quiz = core.get_ai_response(rounds, topic, difficulty, language_name)
        except Exception as e:
            report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
            return redirect(url_for('setup'))
        questions, explanations, source_url = quiz.rounds, quiz.explanations, quiz.source_url
    for _attempt in range(5):
        room = ClassRoom(code=rules.make_code(secrets.SystemRandom()), host_id=user.id, topic=topic[:100],
                         difficulty=difficulty, language=language_name, source_url=source_url,
                         questions_json=json.dumps(questions), explanations_json=json.dumps(explanations),
                         state='lobby', current_round=0, created_at=now)
        db.session.add(room)
        try:
            db.session.commit()
            return redirect(url_for('class_board', code=room.code))
        except IntegrityError:
            db.session.rollback()  # That code is taken: draw another
    abort(500)


@app.route('/class/<code>/board')
def class_board(code):
    room = room_or_404(code)
    require_teacher(room)
    return render_template('class_board.html', room=room, poll_ms=POLL_MS, round_seconds=rules.ROUND_SECONDS,
                           join_url=url_for('class_join_form', _external=True))


@app.route('/class/<code>/<action>', methods=['POST'])
def class_action(code, action):
    """The teacher starts the game, goes on to the next round (or the podium after the last), or ends it early."""
    if action not in ('start', 'next', 'end'):
        abort(404)
    room = room_or_404(code)
    require_teacher(room)
    rounds = len(json.loads(room.questions_json))
    if action == 'start' and room.state == 'lobby':
        room.state, room.current_round, room.round_started_at = 'playing', 0, core.utcnow()
    elif action == 'next' and room.state == 'playing':
        if room.current_round + 1 >= rounds:
            room.state = 'finished'
        else:
            room.current_round, room.round_started_at = room.current_round + 1, core.utcnow()
    elif action == 'end':
        room.state = 'finished'
    db.session.commit()
    return jsonify(ok=True)


@app.route('/class/<code>/kick/<int:player_id>', methods=['POST'])
def class_kick(code, player_id):
    """The teacher removes a nickname (a rude one, or a double)."""
    room = room_or_404(code)
    require_teacher(room)
    ClassPlayer.query.filter_by(room_id=room.id, id=player_id).delete()
    db.session.commit()
    return jsonify(ok=True)


@app.route('/join', methods=['GET', 'POST'])
def class_join_form():
    """Where students type the code shown on the board."""
    if request.method == 'POST':
        code = rules.normalize_code(request.form.get('code'))
        room = ClassRoom.query.filter_by(code=code).first()
        if room and not rules.expired(room.created_at, core.utcnow()):
            return redirect(url_for('class_play', code=room.code))
        flash(_('No game with that code. Check the code on the board.'), 'warning')
    return render_template('class_join.html')


@app.route('/class/<code>', methods=['GET', 'POST'])
def class_play(code):
    """A student's page: pick a nickname, then follow the game."""
    room = room_or_404(code)
    seat = my_seat(room)
    problem = None
    if request.method == 'POST' and not seat:
        nickname = rules.clean_nickname(request.form.get('nickname'))
        if room.state != 'lobby':
            problem = _('This game has already started.')
        elif not nickname:
            problem = _('Please choose a nickname.')
        elif ClassPlayer.query.filter_by(room_id=room.id).count() >= rules.MAX_PLAYERS:
            problem = _('This game is full.')
        else:
            seat = ClassPlayer(room_id=room.id, token=secrets.token_hex(16), nickname=nickname, joined_at=core.utcnow())
            db.session.add(seat)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                seat, problem = None, _('Someone already has that nickname. Try another one.')
            else:
                session[SEATS] = {**session.get(SEATS, {}), room.code: seat.token}
                return redirect(url_for('class_play', code=room.code))
    return render_template('class_play.html', room=room, seat=seat, problem=problem, poll_ms=POLL_MS,
                           round_seconds=rules.ROUND_SECONDS, nickname_length=rules.NICKNAME_LENGTH)


def options(room, index, statements):
    """The round's statements in the same order on every screen of the room."""
    return random.Random(f'{room.code}:{index}').sample(list(statements), len(statements))


@app.route('/class/<code>/state')
def class_state(code):
    """Where the game stands, for the board (teacher) or a student's page."""
    room = room_or_404(code)
    user = current_user()
    teacher = bool(user) and user.id == room.host_id
    seat = None if teacher else my_seat(room)
    if not teacher and not seat:
        abort(404)
    now = core.utcnow()
    players = ClassPlayer.query.filter_by(room_id=room.id).order_by(ClassPlayer.id).all()
    questions = json.loads(room.questions_json)
    index = room.current_round
    key = str(index)
    picks = [json.loads(p.answers_json).get(key) for p in players]
    answered = sum(1 for a in picks if a)
    phase, left = rules.phase(room.state, room.round_started_at, now, answered, len(players))
    payload = {'phase': phase, 'seconds': round(left, 1), 'round': index + 1, 'rounds': len(questions),
               'players': len(players), 'answered': answered}
    if phase == 'lobby':
        payload['lobby'] = [{'id': p.id, 'nickname': p.nickname} for p in players] if teacher else None
    if phase in ('question', 'reveal'):
        statements = questions[index]
        payload['options'] = options(room, index, statements)
    if phase == 'reveal':
        explanations = json.loads(room.explanations_json)
        payload['fake'] = next(s for s, real in statements.items() if not real)
        payload['explanation'] = explanations[index] if index < len(explanations) else None
        payload['counts'] = rules.distribution(statements, [a['pick'] for a in picks if a])
        payload['last'] = index + 1 >= len(questions)
    ranked = rules.ranking([(p.nickname, p.score) for p in players])
    if phase in ('reveal', 'finished'):
        payload['leaderboard'] = [{'rank': r, 'nickname': n, 'score': s}
                                  for r, n, s in ranked[:rules.LEADERBOARD_SIZE if phase == 'reveal' else None]]
    if seat:
        mine = json.loads(seat.answers_json).get(key) if phase in ('question', 'reveal') else None
        rank = next((r for (r, n, s) in ranked if n == seat.nickname), None)
        payload['me'] = {'nickname': seat.nickname, 'score': seat.score, 'rank': rank,
                         'pick': mine['pick'] if mine else None,
                         'correct': mine['correct'] if mine and phase == 'reveal' else None,
                         'points': mine['points'] if mine and phase == 'reveal' else None}
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/class/<code>/answer', methods=['POST'])
def class_answer(code):
    """A student's answer to the open round: once, and only while the question is open."""
    room = room_or_404(code)
    seat = my_seat(room)
    data = request.get_json(silent=True) or {}
    if not seat or room.state != 'playing' or data.get('round') != room.current_round + 1:
        return jsonify(ok=False), 409
    now = core.utcnow()
    elapsed_ms = (now - room.round_started_at).total_seconds() * 1000
    statements = json.loads(room.questions_json)[room.current_round]
    answers = json.loads(seat.answers_json)
    key = str(room.current_round)
    if key in answers or data.get('option') not in statements or elapsed_ms > rules.ROUND_SECONDS * 1000:
        return jsonify(ok=False), 409
    correct = statements[data['option']] is False
    points = rules.points_for(correct, elapsed_ms)
    answers[key] = {'pick': data['option'], 'ms': round(elapsed_ms), 'correct': correct, 'points': points}
    # Written only if the answers are still as read, so a double tap can't count twice
    written = db.session.execute(
        update(ClassPlayer).where(ClassPlayer.id == seat.id, ClassPlayer.answers_json == seat.answers_json)
        .values(answers_json=json.dumps(answers), score=ClassPlayer.score + points)).rowcount == 1
    db.session.commit()
    return jsonify(ok=written), 200 if written else 409


def delete_rooms_of(user):
    """A teacher's rooms and everyone in them (when the teacher deletes their account)."""
    rooms = [r.id for r in ClassRoom.query.filter_by(host_id=user.id).with_entities(ClassRoom.id)]
    if rooms:
        ClassPlayer.query.filter(ClassPlayer.room_id.in_(rooms)).delete(synchronize_session=False)
        ClassRoom.query.filter(ClassRoom.id.in_(rooms)).delete(synchronize_session=False)
