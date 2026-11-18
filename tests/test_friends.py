"""Friends: following players, the Friends tab's weekly leaderboard and feed."""

import app as app_module
import friends as rules
from app import db
from conftest import answers, play_quiz
from models import Follow, QuizSession, User


# ---------- Rules (friends.py) ------------------------------------------------------------------------------------

def test_can_follow():
    assert rules.can_follow(1, 2, False, 0) is None
    assert rules.can_follow(1, 1, False, 0) == 'self'
    assert rules.can_follow(1, 2, True, 0) == 'already'
    assert rules.can_follow(1, 2, False, rules.MAX_FOLLOWING) == 'full'


def test_leaderboard_by_points_then_games():
    board = rules.leaderboard([(1, 30, 2), (2, 60, 1), (3, 30, 5), (4, 0, 0)], me_id=1)
    assert [r['user_id'] for r in board] == [2, 3, 1, 4] and [r['rank'] for r in board] == [1, 2, 3, 4]
    assert [r['is_me'] for r in board] == [False, False, True, False]


def test_suggestions_skip_me_and_who_i_follow():
    assert rules.suggestions([5, 1, 2, 3, 4], {2}, me_id=1, size=2) == [5, 3]


# ---------- In the app --------------------------------------------------------------------------------------------

def user(name):
    return User.query.filter_by(username=name).one()


def follow(client, name):
    return client.post(f'/players/{user(name).id}/follow')


def test_following_from_a_profile(login, app_ctx):
    ann = login('Ann')
    login('Ben')
    html = ann.get(f'/players/{user("Ben").id}').get_data(as_text=True)
    assert 'bi-person-plus-fill' in html and '0 followers' in html
    response = follow(ann, 'Ben')
    assert response.headers['Location'].endswith(f'/players/{user("Ben").id}')
    assert Follow.query.one().follower_id == user('Ann').id
    html = ann.get(f'/players/{user("Ben").id}').get_data(as_text=True)
    assert 'Following' in html and '1 follower' in html and 'You now follow Ben' in html


def test_unfollowing(login, app_ctx):
    ann = login('Ann')
    login('Ben')
    follow(ann, 'Ben')
    ann.post(f'/players/{user("Ben").id}/unfollow')
    assert Follow.query.count() == 0


def test_no_following_yourself_twice_or_too_many(login, app_ctx, monkeypatch):
    ann = login('Ann')
    login('Ben')
    login('Cat')
    follow(ann, 'Ann')
    follow(ann, 'Ben')
    follow(ann, 'Ben')
    assert Follow.query.count() == 1
    monkeypatch.setattr(rules, 'MAX_FOLLOWING', 1)
    follow(ann, 'Cat')
    assert Follow.query.count() == 1


def test_your_own_profile_and_guests_have_no_button(login, app, app_ctx):
    ann = login('Ann')
    assert 'follow-btn' not in ann.get(f'/players/{user("Ann").id}').get_data(as_text=True)
    assert 'follow-btn' not in app.test_client().get(f'/players/{user("Ann").id}').get_data(as_text=True)
    assert app.test_client().post(f'/players/{user("Ann").id}/follow').headers['Location'].endswith('/')


def test_following_is_rate_limited(login, app_ctx, monkeypatch):
    monkeypatch.setitem(app_module.RATE_LIMITS, 'follow', (1, app_module.timedelta(hours=1)))
    ann = login('Ann')
    login('Ben')
    login('Cat')
    follow(ann, 'Ben')
    follow(ann, 'Cat')
    assert Follow.query.count() == 1


def test_the_friends_tab_ranks_you_and_who_you_follow(login, app_ctx):
    ann, ben = login('Ann'), login('Ben')
    login('Dan')
    play_quiz(ann, answers(1, 3))
    play_quiz(ben, answers(3, 3))
    play_quiz(login('Dan'), answers(3, 3))  # Not followed: not on Ann's board
    follow(ann, 'Ben')
    html = ann.get('/friends').get_data(as_text=True)
    board = html.split('This week')[1].split('Active this week')[0]  # Suggestions follow: Ann follows fewer than 3
    assert board.index('>Ben<') < board.index('>Ann<') and '>Dan<' not in board
    assert '>45<' in board and '>15<' in board  # Medium: 15 a correct answer
    assert 'Ben scored 3 of 3' in html


def test_the_feed_links_to_beat_a_shared_game(login, app_ctx):
    ann, ben = login('Ann'), login('Ben')
    play_quiz(ben, answers(2, 3))
    QuizSession.query.one().share_code = 'abc123'
    db.session.commit()
    follow(ann, 'Ben')
    assert '/r/abc123' in ann.get('/friends').get_data(as_text=True)


def test_suggestions_for_someone_who_follows_nobody(login, app_ctx):
    ann = login('Ann')
    play_quiz(login('Ben'), answers(1, 3))
    html = ann.get('/friends').get_data(as_text=True)
    assert 'Active this week' in html and f'/players/{user("Ben").id}/follow' in html


def test_guests_are_asked_to_sign_in(app):
    assert 'Sign in to follow' in app.test_client().get('/friends').get_data(as_text=True)


def test_players_page_has_the_friends_tab(login):
    html = login('Ann').get('/players').get_data(as_text=True)
    assert 'href="/friends"' in html and 'href="/players"' in html


def test_deleting_a_player_removes_their_follows_both_ways(login, app_ctx):
    ann, ben = login('Ann'), login('Ben')
    follow(ann, 'Ben')
    follow(ben, 'Ann')
    app_module.delete_player_data(user('Ann'))
    assert Follow.query.count() == 0
