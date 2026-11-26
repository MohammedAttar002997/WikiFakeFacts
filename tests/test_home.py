"""The home page: today's topic and the top players, without an AI call, and the scroll reveal that can't hide content."""
from app import daily_topic, today_utc
from conftest import answers, play_quiz
from test_daily import play_daily
from translations import translate


def test_home_shows_todays_topic_without_making_the_quiz(app, ai):
    html = app.test_client().get('/').get_data(as_text=True)
    assert daily_topic(today_utc()) in html and 'href="/daily"' in html
    assert ai.quiz_calls == []  # The topic comes from the date; the quiz is only made when someone starts it


def test_home_lists_the_top_players_best_first(app, login, app_ctx):
    play_quiz(login('Sharp'), answers(3, 3))
    play_quiz(login('Shaky'), answers(1, 3))
    html = app.test_client().get('/').get_data(as_text=True)
    ranks = html[html.index('class="home-ranks"'):]
    assert ranks.index('Sharp') < ranks.index('Shaky')
    assert 'href="/leaderboard"' in html


def test_home_without_players_invites_the_first_one(app):
    html = app.test_client().get('/').get_data(as_text=True)
    assert 'class="home-ranks"' not in html and 'Be the first to play and claim the top spot!' in html
    assert 'players today' not in html  # No count while nobody has played today's challenge


def test_home_counts_todays_daily_players(app, login, app_ctx):
    play_daily(login('Early'), 3)
    play_daily(login('Later', language='de'), 2)
    assert '2 players today' in app.test_client().get('/').get_data(as_text=True)
    assert translate('{n} players today', 'de').format(n=2) in login('Reader', language='de').get('/').get_data(as_text=True)


def test_revealed_content_only_hides_when_javascript_runs(app):
    html = app.test_client().get('/').get_data(as_text=True)
    assert "document.documentElement.classList.add('js')" in html  # Set before first paint, in <head>
    with app.open_resource('public/static/css/app.css') as f:
        css = f.read().decode()
    assert '\n[data-reveal] {' not in css and '.js [data-reveal] {' in css
    reduced = css[css.index('/* ---------- Reveal'):]
    assert '@media (prefers-reduced-motion: reduce)' in reduced
