"""Weekly leagues: joining with the first game, points from the week's scores, moving up and down."""
from datetime import datetime, timedelta

import pytest

import app as app_module
import leagues
from app import db
from conftest import answers, play_quiz
from models import LeagueEntry, Score, User

MONDAY = datetime(2026, 11, 16, 10, 0)  # Week 2026-W47
EASY, MEDIUM, HARD = 'Easy (Ages 7-12)', 'Medium (Ages 13-17)', 'Hard (Ages 18-99)'


# ---------- Rules (leagues.py) ------------------------------------------------------------------------------------

def test_points_grow_with_difficulty():
    assert [leagues.points_for(d, 3) for d in (EASY, MEDIUM, HARD)] == [30, 45, 60]
    assert leagues.points_for('Medium', 2) == 30 and leagues.points_for(HARD, 0) == 0


def test_week_range_is_monday_to_monday():
    start, end = leagues.week_range('2026-W47')
    assert start == datetime(2026, 11, 16) and end == datetime(2026, 11, 23)


@pytest.mark.parametrize('size, tier, moving', [
    (20, 1, (5, 5)), (20, 0, (5, 0)), (20, 3, (0, 5)),   # Nobody below Bronze or above Diamond
    (8, 1, (2, 2)), (7, 1, (2, 0)),                       # Small groups don't send anyone down
    (2, 0, (1, 0)), (1, 0, (0, 0)), (0, 0, (0, 0)),
])
def test_zones(size, tier, moving):
    assert leagues.zones(size, tier) == moving


def test_standings_break_ties_by_who_joined_first():
    assert leagues.standings([('a', 30), ('b', 50), ('c', 30)]) == [(1, 'b', 50), (2, 'a', 30), (3, 'c', 30)]


def test_outcomes():
    assert leagues.outcome(1, 100, 20, 1) == 'up' and leagues.outcome(5, 10, 20, 1) == 'up'
    assert leagues.outcome(6, 10, 20, 1) == 'stay' and leagues.outcome(15, 0, 20, 1) == 'stay'
    assert leagues.outcome(16, 0, 20, 1) == 'down' and leagues.outcome(20, 5, 20, 1) == 'down'
    assert leagues.outcome(1, 0, 20, 1) == 'stay'  # Nobody moves up without points


def test_next_tier_stays_on_the_ladder():
    assert leagues.next_tier(0, 'up') == 1 and leagues.next_tier(0, 'down') == 0
    assert leagues.next_tier(3, 'up') == 3 and leagues.next_tier(2, 'stay') == 2


def test_groups_fill_before_a_new_one_opens():
    assert leagues.next_group([]) == 0 and leagues.next_group([5]) == 0
    assert leagues.next_group([leagues.GROUP_SIZE]) == 1 and leagues.next_group([leagues.GROUP_SIZE, 3]) == 1


# ---------- In the app --------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = MONDAY


@pytest.fixture
def clock(monkeypatch):
    """The league's week comes from app.utcnow; scores are stamped with the same clock."""
    c = Clock()
    monkeypatch.setattr(app_module, 'utcnow', lambda: c.now)
    return c


def played(name, points_scores, when, tier=0, group_no=0, week=None):
    """A player with a league entry and scores at `when`: points_scores is [(difficulty, correct)]."""
    user = User.query.filter_by(username=name).first() or User(username=name)
    db.session.add(user)
    db.session.commit()
    db.session.add(LeagueEntry(user_id=user.id, week=week or app_module.event_week(when.date()), tier=tier,
                               group_no=group_no))
    for difficulty, correct in points_scores:
        db.session.add(Score(user_id=user.id, topic='Cat', difficulty=difficulty, score=correct, total_questions=3,
                             percentage=correct / 3 * 100, timestamp=when))
    db.session.commit()
    return user


def test_the_first_finished_game_joins_the_league(login, clock, app_ctx):
    client = login('Ann')
    assert LeagueEntry.query.count() == 0
    play_quiz(client, answers(2, 3))
    entry = LeagueEntry.query.one()
    assert (entry.week, entry.tier, entry.group_no) == ('2026-W47', 0, 0)
    play_quiz(client, answers(3, 3))
    assert LeagueEntry.query.count() == 1  # Once a week


def test_the_page_shows_your_group_and_points(login, clock, app_ctx):
    client = login('Ann')
    Score.query.delete()
    play_quiz(client, answers(2, 3))
    for score in Score.query:
        score.timestamp = MONDAY
    played('Ben', [(HARD, 3)], MONDAY)
    db.session.commit()
    html = client.get('/league').get_data(as_text=True)
    assert 'Weekly league' in html and 'Bronze' in html
    ben, ann = html.index('>Ben<'), html.index('>Ann<')
    assert ben < ann  # 60 points beat 30
    # Points shown and the promotion line drawn (the case file calls it "Promotion", themes.py)
    assert '>60<' in html and '>30<' in html and 'league-line is-up' in html and 'Promotion' in html


def test_results_show_the_games_league_points(login, clock):
    assert '+30 league points' in play_quiz(login('Ann'), answers(2, 3))  # Medium: 15 a correct answer


def test_scores_from_other_weeks_dont_count(login, clock, app_ctx):
    from views.leagues import group_standings
    played('Ann', [(HARD, 3)], MONDAY)
    ann = User.query.filter_by(username='Ann').one()
    db.session.add(Score(user_id=ann.id, topic='Cat', difficulty=HARD, score=3, total_questions=3, percentage=100,
                         timestamp=MONDAY - timedelta(days=3)))
    db.session.commit()
    assert group_standings('2026-W47', 0, 0)[0]['points'] == 60


def test_the_winner_moves_up_next_week_and_is_told(login, clock, app_ctx):
    last_week = MONDAY - timedelta(days=7)
    played('Ann', [(HARD, 3)], last_week)
    played('Ben', [(EASY, 1)], last_week)
    clock.now = MONDAY
    client = login('Ann')
    html = client.get('/league').get_data(as_text=True)
    assert 'moved up' in html and 'Silver' in html
    play_quiz(client, answers(1, 3))
    this_week = LeagueEntry.query.filter_by(week='2026-W47').one()
    assert this_week.tier == 1
    play_quiz(login('Ben'), answers(1, 3))
    assert LeagueEntry.query.filter_by(week='2026-W47').count() == 2
    assert {e.tier for e in LeagueEntry.query.filter_by(week='2026-W47')} == {0, 1}  # Ben stays in Bronze


def test_the_last_of_a_big_group_moves_down(clock, app_ctx):
    from views.leagues import tier_for
    last_week = MONDAY - timedelta(days=7)
    for i in range(8):
        played(f'P{i}', [(HARD, 8 - i)], last_week, tier=1)
    assert tier_for(User.query.filter_by(username='P7').one(), '2026-W47') == 0
    assert tier_for(User.query.filter_by(username='P0').one(), '2026-W47') == 2
    assert tier_for(User.query.filter_by(username='P4').one(), '2026-W47') == 1


def test_a_full_group_opens_a_new_one(login, clock, monkeypatch, app_ctx):
    monkeypatch.setattr(leagues, 'GROUP_SIZE', 2)
    for name in ('Ann', 'Ben', 'Cat'):
        play_quiz(login(name), answers(1, 3))
    assert sorted(e.group_no for e in LeagueEntry.query) == [0, 0, 1]


def test_practice_doesnt_join_the_league(login, clock, app_ctx):
    client = login('Ann')
    play_quiz(client, answers(1, 3))
    LeagueEntry.query.delete()
    db.session.commit()
    client.post('/practice/mistakes')
    from models import QuizSession
    quiz = QuizSession.query.filter_by(practice=True).one()
    import json
    for statements in json.loads(quiz.questions_json):
        client.post('/quiz', data={'option': next(s for s, real in statements.items() if not real)})
    assert LeagueEntry.query.count() == 0


def test_guests_and_new_players_see_how_to_join(login, clock, app):
    assert 'Sign in' in app.test_client().get('/league').get_data(as_text=True)
    html = login('Ann').get('/league').get_data(as_text=True)
    assert 'Finish any game to join a group' in html and 'How the league works' in html


def test_the_league_is_a_challenges_tab(login, clock):
    assert 'href="/league"' in login('Ann').get('/daily').get_data(as_text=True)


def test_deleting_a_player_removes_their_entries(login, clock, app_ctx):
    play_quiz(login('Ann'), answers(1, 3))
    app_module.delete_player_data(User.query.filter_by(username='Ann').one())
    assert LeagueEntry.query.count() == 0
