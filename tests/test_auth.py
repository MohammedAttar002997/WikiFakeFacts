"""Player names are protected by a 4-digit PIN."""
from app import db
from models import User


def sign_in(app, name, pin):
    client = app.test_client()
    response = client.post('/setup', data={'username': name, 'pin': pin})
    with client.session_transaction() as sess:
        return response, sess.get('username')


def test_new_player_picks_a_pin(app, app_ctx):
    response, signed_in_as = sign_in(app, 'Phoenix', '1234')
    assert response.status_code == 200
    assert signed_in_as == 'Phoenix'
    assert User.query.filter_by(username='Phoenix').one().pin_hash != '1234'  # stored hashed


def test_wrong_pin_is_rejected(app):
    sign_in(app, 'Phoenix', '1234')
    response, signed_in_as = sign_in(app, 'Phoenix', '9999')
    assert response.headers['Location'] == '/'
    assert signed_in_as is None


def test_names_match_case_insensitively(app):
    sign_in(app, 'Phoenix', '1234')
    assert sign_in(app, 'phoenix', '0000')[1] is None
    # The right PIN with other capitalisation signs in under the original spelling
    assert sign_in(app, 'PHOENIX ', '1234')[1] == 'Phoenix'


def test_pin_must_be_four_digits(app):
    assert sign_in(app, 'Phoenix', '12a4')[1] is None
    assert sign_in(app, 'Phoenix', '123')[1] is None


def test_player_from_before_pins_claims_name_with_first_pin(app, app_ctx, make_user):
    make_user('Legacy')
    assert sign_in(app, 'Legacy', '4321')[1] == 'Legacy'
    assert sign_in(app, 'Legacy', '1111')[1] is None


def test_too_many_wrong_pins_lock_the_name(app):
    sign_in(app, 'Phoenix', '1234')
    for _ in range(5):
        sign_in(app, 'Phoenix', '0001')
    assert sign_in(app, 'Phoenix', '1234')[1] is None  # locked even with the right PIN


def test_logout_switches_player(login):
    client = login('Phoenix')
    client.get('/logout')
    with client.session_transaction() as sess:
        assert 'username' not in sess
