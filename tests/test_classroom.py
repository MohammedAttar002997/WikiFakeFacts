"""Classroom mode: a teacher's board, students with nicknames, rounds paced by the teacher, nothing kept afterwards."""
import random
from datetime import datetime, timedelta

import pytest

import app as app_module
import classroom as rules
from app import db
from models import ClassPlayer, ClassRoom, User

T0 = datetime(2026, 11, 17, 9, 0)


# ---------- Rules (classroom.py) ----------------------------------------------------------------------------------

def test_codes_are_easy_to_read_and_type():
    code = rules.make_code(random.Random(1))
    assert len(code) == rules.CODE_LENGTH and not set(code) & set('ILO01')
    assert rules.normalize_code(' abc-12 3 ') == 'ABC123'


@pytest.mark.parametrize('typed, shown', [('  Ann   B ', 'Ann B'), ('x' * 30, 'x' * 20), ('   ', None), ('!!!', None)])
def test_nicknames(typed, shown):
    assert rules.clean_nickname(typed) == shown


def test_points_reward_speed():
    assert rules.points_for(False, 100) == 0
    assert rules.points_for(True, 0) == rules.BASE_POINTS + rules.SPEED_BONUS
    assert rules.points_for(True, rules.ROUND_SECONDS * 1000) == rules.BASE_POINTS


def test_a_question_closes_on_time_or_when_everyone_answered():
    assert rules.phase('lobby', None, T0, 0, 3) == ('lobby', 0)
    assert rules.phase('playing', T0, T0 + timedelta(seconds=5), 1, 3)[0] == 'question'
    assert rules.phase('playing', T0, T0 + timedelta(seconds=5), 3, 3) == ('reveal', 0)
    assert rules.phase('playing', T0, T0 + timedelta(seconds=rules.ROUND_SECONDS + 1), 0, 3) == ('reveal', 0)
    assert rules.phase('finished', T0, T0, 0, 3) == ('finished', 0)


def test_ranking_shares_ranks_on_ties():
    assert rules.ranking([('Ann', 500), ('Ben', 900), ('Cat', 500), ('Dan', 0)]) == [
        (1, 'Ben', 900), (2, 'Ann', 500), (2, 'Cat', 500), (4, 'Dan', 0)]


def test_distribution_counts_every_option():
    assert rules.distribution({'A': True, 'B': False}, ['B', 'B', 'X']) == {'A': 0, 'B': 2}


# ---------- In the app --------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = T0


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(app_module, 'utcnow', lambda: c.now)
    return c


@pytest.fixture
def room(login, clock, app_ctx):
    """A teacher's 3-round room: (teacher client, room)."""
    teacher = login('Teacher')
    response = teacher.post('/class/new', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'})
    room = ClassRoom.query.one()
    assert response.headers['Location'].endswith(f'/class/{room.code}/board')
    return teacher, room


def student(app, room, nickname):
    client = app.test_client()
    client.post(f'/class/{room.code}', data={'nickname': nickname})
    return client


def state(client, room):
    return client.get(f'/class/{room.code}/state').get_json()


def act(teacher, room, action):
    return teacher.post(f'/class/{room.code}/{action}')


def fake_of(room, index):
    import json
    return next(s for s, real in json.loads(room.questions_json)[index].items() if not real)


def test_the_board_shows_the_code_and_the_join_address(room):
    teacher, room = room
    html = teacher.get(f'/class/{room.code}/board').get_data(as_text=True)
    assert room.code in html and '/join' in html


def test_students_join_with_the_code_and_a_nickname(app, room):
    teacher, room = room
    response = app.test_client().post('/join', data={'code': room.code.lower()})
    assert response.headers['Location'].endswith(f'/class/{room.code}')
    student(app, room, 'Ann')
    student(app, room, 'Ben')
    lobby = state(teacher, room)
    assert lobby['phase'] == 'lobby' and [p['nickname'] for p in lobby['lobby']] == ['Ann', 'Ben']


def test_a_wrong_code_says_so(app, room):
    assert 'No game with that code' in app.test_client().post('/join', data={'code': 'ZZZZZZ'}).get_data(as_text=True)


def test_nicknames_are_unique_per_room(app, room):
    _, room = room
    student(app, room, 'Ann')
    html = app.test_client().post(f'/class/{room.code}', data={'nickname': 'Ann'}).get_data(as_text=True)
    assert 'already has that nickname' in html and ClassPlayer.query.count() == 1


def test_no_joining_once_started_or_full(app, room, monkeypatch):
    teacher, room = room
    monkeypatch.setattr(rules, 'MAX_PLAYERS', 1)
    student(app, room, 'Ann')
    assert 'This game is full' in app.test_client().post(
        f'/class/{room.code}', data={'nickname': 'Ben'}).get_data(as_text=True)
    act(teacher, room, 'start')
    monkeypatch.setattr(rules, 'MAX_PLAYERS', 60)
    assert 'already started' in app.test_client().post(
        f'/class/{room.code}', data={'nickname': 'Cat'}).get_data(as_text=True)


def test_a_whole_game(app, room, clock):
    teacher, room = room
    ann, ben = student(app, room, 'Ann'), student(app, room, 'Ben')
    act(teacher, room, 'start')
    question = state(ann, room)
    assert question['phase'] == 'question' and question['round'] == 1 and len(question['options']) == 4
    assert 'fake' not in question  # Not before the reveal
    assert state(ben, room)['options'] == question['options']  # Same order on every screen

    clock.now = T0 + timedelta(seconds=2)
    ann.post(f'/class/{room.code}/answer', json={'round': 1, 'option': fake_of(room, 0)})
    assert state(teacher, room)['answered'] == 1 and state(teacher, room)['phase'] == 'question'
    ben.post(f'/class/{room.code}/answer', json={'round': 1, 'option': 'R0 true A'})
    reveal = state(teacher, room)  # Everyone answered: no waiting for the timer
    assert reveal['phase'] == 'reveal' and reveal['fake'] == fake_of(room, 0)
    assert reveal['counts'][fake_of(room, 0)] == 1 and reveal['counts']['R0 true A'] == 1
    assert [r['nickname'] for r in reveal['leaderboard']] == ['Ann', 'Ben']
    me = state(ann, room)['me']
    assert me['correct'] is True and me['points'] == rules.points_for(True, 2000) and me['rank'] == 1

    for n in (2, 3):
        act(teacher, room, 'next')
        assert state(ann, room)['round'] == n
    clock.now += timedelta(seconds=rules.ROUND_SECONDS + 1)  # Nobody answered the last round: closes on time
    assert state(teacher, room)['phase'] == 'reveal' and state(teacher, room)['last'] is True
    act(teacher, room, 'next')
    final = state(teacher, room)
    assert final['phase'] == 'finished' and final['leaderboard'][0]['nickname'] == 'Ann'
    assert state(ben, room)['me']['rank'] == 2


def test_answers_count_once_and_only_while_open(app, room, clock):
    teacher, room = room
    ann = student(app, room, 'Ann')
    student(app, room, 'Ben')
    assert ann.post(f'/class/{room.code}/answer', json={'round': 1, 'option': fake_of(room, 0)}).status_code == 409
    act(teacher, room, 'start')
    ann.post(f'/class/{room.code}/answer', json={'round': 1, 'option': fake_of(room, 0)})
    assert ann.post(f'/class/{room.code}/answer', json={'round': 1, 'option': fake_of(room, 0)}).status_code == 409
    assert ann.post(f'/class/{room.code}/answer', json={'round': 1, 'option': 'Made up'}).status_code == 409
    score = ClassPlayer.query.filter_by(nickname='Ann').one().score
    assert score == rules.points_for(True, 0)
    clock.now = T0 + timedelta(seconds=rules.ROUND_SECONDS + 1)
    ben = app.test_client()
    with ben.session_transaction() as s:
        s['class_seats'] = {room.code: ClassPlayer.query.filter_by(nickname='Ben').one().token}
    assert ben.post(f'/class/{room.code}/answer', json={'round': 1, 'option': fake_of(room, 0)}).status_code == 409


def test_only_the_teacher_runs_the_game(app, room, login):
    teacher, room = room
    ann = student(app, room, 'Ann')
    assert act(ann, room, 'start').status_code == 404
    assert act(login('Someone'), room, 'start').status_code == 404
    assert login('Someone').get(f'/class/{room.code}/board').status_code == 404
    assert app.test_client().get(f'/class/{room.code}/state').status_code == 404  # Not joined
    assert state(ann, room)['lobby'] is None  # Students don't get the list with ids


def test_the_teacher_can_remove_a_nickname(app, room):
    teacher, room = room
    student(app, room, 'Rude name')
    player = ClassPlayer.query.one()
    teacher.post(f'/class/{room.code}/kick/{player.id}')
    assert ClassPlayer.query.count() == 0


def test_the_teacher_can_end_early(app, room):
    teacher, room = room
    student(app, room, 'Ann')
    act(teacher, room, 'start')
    act(teacher, room, 'end')
    assert state(teacher, room)['phase'] == 'finished'


def test_old_rooms_and_nicknames_are_deleted(app, room, clock, login):
    teacher, room = room
    student(app, room, 'Ann')
    clock.now = T0 + timedelta(hours=rules.ROOM_HOURS + 1)
    assert app.test_client().get(f'/class/{room.code}').status_code == 404
    teacher.post('/class/new', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'})
    assert ClassRoom.query.count() == 1 and ClassPlayer.query.count() == 0


def test_a_ready_quiz_is_used_when_there_is_one(login, clock, ai, app_ctx):
    import ready_pool
    from default_text import CATEGORIES, DIFFICULTY_OPTIONS
    from models import ReadyQuiz
    kind = ready_pool.Kind(CATEGORIES['1'], DIFFICULTY_OPTIONS['2'], 3, 'English')
    db.session.add(ReadyQuiz(**kind._asdict(), topic='Ready Topic', questions_json='[{"A": true, "B": false}]',
                             explanations_json='["Why."]'))
    db.session.commit()
    login('Teacher').post('/class/new', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'})
    assert ClassRoom.query.one().topic == 'Ready Topic' and ai.quiz_calls == []


def test_deleting_the_teacher_deletes_their_rooms(app, room):
    _, room = room
    student(app, room, 'Ann')
    app_module.delete_player_data(User.query.filter_by(username='Teacher').one())
    assert ClassRoom.query.count() == 0 and ClassPlayer.query.count() == 0


def test_setup_and_home_link_to_the_class_game(login, app):
    assert '/class/new' in login('Teacher').get('/setup').get_data(as_text=True)
    assert '/join' in app.test_client().get('/').get_data(as_text=True)
