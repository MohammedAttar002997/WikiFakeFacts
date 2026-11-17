"""Classroom follow-ups: the teacher's results download and playing again with the same class."""
import csv
import io
import json
import random

import pytest

import classroom as rules
from models import ClassPlayer, ClassRoom
from test_classroom import act, clock, fake_of, room, state, student  # noqa: F401  (fixtures and helpers)


def answer(client, room, round_number, fake):
    """Answer the open round: the fake (right) or a true statement (wrong)."""
    statements = json.loads(room.questions_json)[round_number - 1]
    pick = fake_of(room, round_number - 1) if fake else next(s for s, real in statements.items() if real)
    return client.post(f'/class/{room.code}/answer', json={'round': round_number, 'option': pick})


# ---------- Rules (classroom.py) ----------------------------------------------------------------------------------

def test_results_table_ranks_and_lists_every_round():
    rows = rules.results_table(3, [
        ('Ann', 900, {'0': {'correct': True, 'points': 900}, '1': {'correct': False, 'points': 0}}),
        ('Ben', 1400, {'0': {'correct': True, 'points': 700}, '2': {'correct': True, 'points': 700}}),
        ('Cat', 0, {}),
    ])
    assert rows[0] == ['Rank', 'Nickname', 'Score', 'Correct', 'Round 1', 'Round 2', 'Round 3']
    assert rows[1:] == [[1, 'Ben', 1400, 2, 700, '', 700], [2, 'Ann', 900, 1, 900, 0, ''], [3, 'Cat', 0, 0, '', '', '']]


def test_next_topic_stays_in_the_category_unless_surprise():
    topics = {'Animals': ['Cat', 'Dog'], 'Space': ['Moon']}
    assert rules.next_topic({'category': 'Animals'}, topics, random.Random(1))[0] == 'Animals'
    categories = {rules.next_topic({'category': 'Animals', 'surprise': True}, topics, random.Random(i))[0]
                  for i in range(20)}
    assert categories == {'Animals', 'Space'}


# ---------- Results download --------------------------------------------------------------------------------------

def test_the_teacher_downloads_the_results(app, room, clock):
    teacher, room = room
    ann, ben = student(app, room, 'Ann'), student(app, room, 'Ben')
    act(teacher, room, 'start')
    answer(ann, room, 1, fake=True)
    answer(ben, room, 1, fake=False)
    response = teacher.get(f'/class/{room.code}/results.csv')
    assert response.status_code == 200 and response.mimetype == 'text/csv'
    assert 'attachment' in response.headers['Content-Disposition']
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip('﻿'))))
    assert rows[0][:4] == ['Rank', 'Nickname', 'Score', 'Correct'] and len(rows[0]) == 4 + 3
    assert [r[1] for r in rows[1:]] == ['Ann', 'Ben'] and rows[1][3] == '1' and rows[2][4] == '0'


def test_only_the_teacher_downloads(app, room, login):
    _, room = room
    assert student(app, room, 'Ann').get(f'/class/{room.code}/results.csv').status_code == 404
    assert login('Someone').get(f'/class/{room.code}/results.csv').status_code == 404


# ---------- Play again --------------------------------------------------------------------------------------------

def test_play_again_seats_the_class_in_a_new_game(app, room, clock, ai):
    teacher, room = room
    ann = student(app, room, 'Ann')
    student(app, room, 'Ben')
    act(teacher, room, 'start')
    answer(ann, room, 1, fake=True)
    act(teacher, room, 'end')
    assert state(teacher, room)['can_play_again'] is True
    response = teacher.post(f'/class/{room.code}/again').get_json()
    new_room = ClassRoom.query.filter(ClassRoom.id != room.id).one()
    assert response['board'].endswith(f'/class/{new_room.code}/board')
    assert sorted(p.nickname for p in ClassPlayer.query.filter_by(room_id=new_room.id)) == ['Ann', 'Ben']
    assert {p.score for p in ClassPlayer.query.filter_by(room_id=new_room.id)} == {0}
    assert json.loads(new_room.settings_json) == json.loads(room.settings_json)
    # The students' pages see where to go, and are recognised there without joining again
    assert state(ann, room)['next'] == new_room.code
    new_state = ann.get(f'/class/{new_room.code}/state').get_json()
    assert new_state['phase'] == 'lobby' and new_state['me']['nickname'] == 'Ann' and new_state['me']['score'] == 0
    # A second click doesn't make a third room
    assert teacher.post(f'/class/{room.code}/again').get_json()['board'].endswith(new_room.code + '/board')
    assert ClassRoom.query.count() == 2


def test_play_again_only_after_the_game(app, room):
    teacher, room = room
    student(app, room, 'Ann')
    assert teacher.post(f'/class/{room.code}/again').status_code == 409


def test_rooms_from_before_play_again_dont_offer_it(app, room):
    teacher, room = room
    room.settings_json = None
    room.state = 'finished'
    from app import db
    db.session.commit()
    assert state(teacher, room)['can_play_again'] is False
    assert teacher.post(f'/class/{room.code}/again').status_code == 409


def test_students_cant_play_again(app, room):
    teacher, room = room
    ann = student(app, room, 'Ann')
    act(teacher, room, 'end')
    assert ann.post(f'/class/{room.code}/again').status_code == 404
