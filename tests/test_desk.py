"""The detective's desk at the top of /setup (views/desk.py): today's case and the player's notes."""
import app as app_module
from conftest import answers, play_quiz
from views import desk as desk_views


def play_daily(client, right):
    client.post('/daily/start')
    for i in range(app_module.DAILY_ROUNDS):
        client.post('/quiz', data={'option': f'R{i} FAKE' if i < right else f'R{i} true A'})


def desk_html(client):
    html = client.get('/setup').get_data(as_text=True)
    return html.split('class="desk"')[1].split('class="desk-subhead"')[0]


def test_case_number_is_the_month_and_day():
    assert desk_views.case_number('2026-11-25') == '1125'
    assert desk_views.case_number('2027-01-03') == '0103'


def test_a_new_day_starts_with_an_open_case(login, ai):
    html = desk_html(login('Ann'))
    assert 'Welcome back, detective.' in html and 'One new case on your desk.' in html
    assert app_module.daily_topic(app_module.today_utc()) in html
    assert f'Case No. {desk_views.case_number(app_module.today_utc())}' in html
    assert '>Open<' in html and 'Open the case' in html and 'href="/daily"' in html
    assert 'Be the first detective on it today' in html


def test_after_playing_the_case_is_closed_and_counted(login, ai, app_ctx):
    ann, ben = login('Ann'), login('Ben')
    play_daily(ann, app_module.DAILY_ROUNDS)  # Every fake found
    play_daily(ben, 1)
    assert desk_views.daily_counts(app_module.today_utc()) == (2, 1)
    html = desk_html(ann)
    assert '>Closed<' in html and 'See your report' in html and "Today's case is closed" in html
    assert f'Your result: {app_module.DAILY_ROUNDS} of {app_module.DAILY_ROUNDS}' in html
    assert '2 detectives on it today' in html and '1 cracked it' in html


def test_the_notes_show_streak_and_league(login, ai):
    client = login('Ann')
    html = desk_html(client)
    assert '0' in html and 'days in a row' in html and "Finish a quiz to join this week's league" in html
    play_quiz(client, answers(2, 3))
    html = desk_html(client)
    assert '<b>1</b>day in a row' in html.replace('\n', '').replace('  ', '')
    assert '#1' in html and 'Bronze league' in html and 'href="/league"' in html and 'href="/event"' in html


def test_the_tour_still_finds_the_daily_challenge(login):
    html = login('Ann').get('/setup').get_data(as_text=True)
    assert html.count('data-tour="daily"') == 1 and 'class="case-folder" data-tour="daily"' in html
