"""Weekly events: a featured category each week, with a leaderboard of each player's best event quizzes."""
from datetime import date, timedelta

import app as app_module
from app import db, event_standings, event_theme, event_week
from conftest import answers, play_quiz
from default_text import MENU
from models import QuizSession, UserBadge


def play_event(client, right):
    client.post('/event/start')
    for pick in answers(right, app_module.EVENT_ROUNDS):
        client.post('/quiz', data={'option': pick})


def test_weeks_run_monday_to_sunday_and_each_has_its_own_theme():
    monday = date(2026, 11, 2)
    assert event_week(monday) == event_week(monday + timedelta(days=6)) == '2026-W45'
    assert event_week(monday + timedelta(days=7)) == '2026-W46'
    assert event_week(date(2027, 1, 1)) == '2026-W53'  # ISO weeks can belong to the previous year
    assert event_theme(monday) == event_theme(monday + timedelta(days=6))
    themes = [event_theme(monday + timedelta(weeks=w)) for w in range(len(MENU))]
    assert set(themes) == set(MENU)  # every category in turn, so never the same theme two weeks running


def test_event_quizzes_use_the_theme_and_a_fixed_format(login, ai, app_ctx):
    play_event(login('Player'), 3)
    quiz = QuizSession.query.one()
    assert quiz.event_week == event_week() and quiz.topic in MENU[event_theme()]
    assert (ai.quiz_calls[0]['n'], ai.quiz_calls[0]['difficulty']) == (5, app_module.EVENT_DIFFICULTY)


def test_topics_dont_repeat_within_a_week_while_there_are_new_ones(login, app_ctx):
    client = login('Player')
    for _ in range(4):
        play_event(client, 1)
    topics = [q.topic for q in QuizSession.query.all()]
    assert len(set(topics)) == 4


def test_standings_add_up_the_best_three_quizzes(login, app_ctx):
    grinder = login('Grinder')
    for right in (2, 2, 2, 2, 2):
        play_event(grinder, right)
    sharp = login('Sharp')
    for right in (5, 4, 1, 0):
        play_event(sharp, right)
    quick = login('Quick')
    for right in (5, 5):
        play_event(quick, right)
    standings = [(row['user'].username, row['points'], row['quizzes']) for row in event_standings(event_week())]
    # Sharp and Quick both have 10; Quick needed fewer quizzes
    assert standings == [('Quick', 10, 2), ('Sharp', 10, 4), ('Grinder', 6, 5)]


def test_only_event_quizzes_count(login, app_ctx):
    client = login('Player')
    play_quiz(client, answers(3, 3), category='1')
    play_event(login('Friend'), 4)
    friend_quiz = QuizSession.query.filter(QuizSession.event_week.isnot(None)).one()
    friend_quiz.share_code = 'friendcode'
    db.session.commit()
    client.post('/r/friendcode/play')  # replaying an event quiz as a challenge doesn't count either
    for pick in answers(5, 5):
        client.post('/quiz', data={'option': pick})
    assert [row['user'].username for row in event_standings(event_week())] == ['Friend']


def test_event_page_shows_theme_standings_and_last_weeks_champion(app, login, app_ctx):
    play_event(login('Champ'), 5)
    for quiz in QuizSession.query.all():
        quiz.event_week = event_week(app_module.utcnow().date() - timedelta(days=7))
    db.session.commit()
    play_event(login('Player'), 3)

    html = login('Player', create=False).get('/event').get_data(as_text=True)
    assert event_theme() in html and "Last week's champion" in html and 'Champ' in html
    assert '3 / 15' in html and 'Play another event quiz' in html
    assert html.index('Player') < html.index("Last week's champion")


def test_signed_out_players_sign_in_first(app):
    response = app.test_client().post('/event/start')
    assert response.headers['Location'] == '/'
    assert 'Weekly event' in app.test_client().get('/event').get_data(as_text=True)


def test_results_and_setup_link_to_the_event(login, app_ctx):
    client = login('Player')
    play_event(client, 2)
    assert 'Weekly event standings' in client.get('/results').get_data(as_text=True)
    assert "This week's theme: " + event_theme() in client.get('/setup').get_data(as_text=True)


def test_three_event_quizzes_in_a_week_earn_a_badge(login, app_ctx):
    client = login('Player')
    for _ in range(2):
        play_event(client, 1)
    assert not UserBadge.query.filter_by(badge='event').first()
    play_event(client, 1)
    assert UserBadge.query.filter_by(badge='event').one()


def test_one_challenges_tab_leads_to_both_daily_and_weekly(app):
    client = app.test_client()
    for page in ('/daily', '/event'):
        html = client.get(page).get_data(as_text=True)
        # The navbar tab is active on both pages, and the switch marks the one you're on
        assert 'class="nav-link active" href="/daily"' in html
        assert f'class="nav-link active" href="{page}" aria-current="page"' in html
        assert 'href="/daily"' in html and 'href="/event"' in html
