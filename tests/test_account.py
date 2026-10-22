"""The account page (change PIN, delete account) and the admin commands."""
import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from conftest import answers, play_quiz, sign_in_as, signed_in_name
from models import (ChatbotSession, CustomContent, DailyChallenge, PlayerAnalysis, QuizSession, Score, User,
                    UserBadge)


@pytest.fixture
def player(login, app_ctx):
    """'Player' with PIN 1234, a finished quiz (so scores, badges and a saved analysis) and an upload."""
    client = login('Player')
    user = User.query.filter_by(username='Player').one()
    user.pin_hash = generate_password_hash('1234')
    db.session.add(CustomContent(user_id=user.id, title='Notes', content='x', content_type='url'))
    db.session.commit()
    sign_in_as(client, 'Player')  # Setting the PIN ended the session, as changing it does in the app
    play_quiz(client, answers(3, 3))
    client.get('/analysis')
    return client


def pin_of(name):
    return User.query.filter_by(username=name).one().pin_hash


def test_account_page_needs_sign_in(app):
    assert app.test_client().get('/account').headers['Location'] == '/'


def test_account_page_lists_what_would_be_deleted(player):
    html = player.get('/account').get_data(as_text=True)
    assert '1 quiz result' in html and '1 uploaded document or page' in html


def test_change_pin(player):
    player.post('/account/pin', data={'current_pin': '1234', 'new_pin': '5678'})
    assert check_password_hash(pin_of('Player'), '5678')


def test_change_pin_needs_the_current_pin(player):
    player.post('/account/pin', data={'current_pin': '0000', 'new_pin': '5678'})
    assert check_password_hash(pin_of('Player'), '1234')
    assert "isn&#39;t right" in player.get('/account').get_data(as_text=True)


def test_new_pin_must_be_four_digits(player):
    player.post('/account/pin', data={'current_pin': '1234', 'new_pin': '12'})
    assert check_password_hash(pin_of('Player'), '1234')


def test_wrong_pins_on_account_page_count_toward_lockout(player):
    for _ in range(5):
        player.post('/account/pin', data={'current_pin': '0000', 'new_pin': '5678'})
    player.post('/account/pin', data={'current_pin': '1234', 'new_pin': '5678'})
    assert check_password_hash(pin_of('Player'), '1234')  # locked, so even the right PIN is refused


def test_delete_needs_confirmation_and_pin(player):
    player.post('/account/delete', data={'pin': '1234'})                  # box not ticked
    player.post('/account/delete', data={'pin': '0000', 'confirm': '1'})  # wrong PIN
    assert User.query.filter_by(username='Player').count() == 1


def test_delete_removes_only_this_players_data(player, login):
    other = login('Other')
    play_quiz(other, answers(1, 3))
    with player.session_transaction() as sess:
        sess['chat_id'] = 'player-chat'
    db.session.add(ChatbotSession(session_id='player-chat', conversation_json='[]'))
    db.session.add(DailyChallenge(date='2026-10-21', language='English', topic='Cat', questions_json='[]',
                                  explanations_json='[]'))
    db.session.commit()
    player_id = User.query.filter_by(username='Player').one().id

    response = player.post('/account/delete', data={'pin': '1234', 'confirm': '1'})

    assert response.headers['Location'] == '/'
    for model in (Score, QuizSession, CustomContent, UserBadge, PlayerAnalysis):
        assert model.query.filter_by(user_id=player_id).count() == 0, model.__name__
    assert db.session.get(User, player_id) is None
    assert ChatbotSession.query.filter_by(session_id='player-chat').count() == 0
    assert DailyChallenge.query.count() == 1                       # shared with other players
    assert Score.query.count() == 1 and User.query.count() == 1    # Other's data is untouched
    assert signed_in_name(player) is None


def test_deleted_name_can_be_claimed_again(player, app):
    player.post('/account/delete', data={'pin': '1234', 'confirm': '1'})
    client = app.test_client()
    client.post('/setup', data={'username': 'Player', 'pin': '9999'})
    assert signed_in_name(client) == 'Player'


def test_reset_pin_command(app, player):
    result = app.test_cli_runner().invoke(args=['reset-pin', 'player', '--pin', '4321'])
    assert 'New PIN for Player: 4321' in result.output
    db.session.expire_all()
    assert check_password_hash(pin_of('Player'), '4321')


def test_reset_pin_command_generates_a_random_pin(app, player):
    result = app.test_cli_runner().invoke(args=['reset-pin', 'Player'])
    new_pin = result.output.strip().rsplit(' ', 1)[-1]
    assert len(new_pin) == 4 and new_pin.isdigit()
    db.session.expire_all()
    assert check_password_hash(pin_of('Player'), new_pin)


def test_reset_pin_command_rejects_unknown_player_and_bad_pin(app, player):
    runner = app.test_cli_runner()
    assert runner.invoke(args=['reset-pin', 'Nobody']).exit_code != 0
    assert runner.invoke(args=['reset-pin', 'Player', '--pin', 'abcd']).exit_code != 0


def test_delete_player_command(app, player):
    result = app.test_cli_runner().invoke(args=['delete-player', 'Player', '--yes'])
    assert result.exit_code == 0
    db.session.expire_all()
    assert User.query.filter_by(username='Player').count() == 0
    assert Score.query.count() == 0
