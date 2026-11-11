"""Player names are protected by a 4-digit PIN."""
from app import db
from conftest import signed_in_name
from models import User


def sign_in(app, name, pin):
    client = app.test_client()
    response = client.post('/setup', data={'username': name, 'pin': pin})
    return response, signed_in_name(client)


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
    assert signed_in_name(client) is None
    with client.session_transaction() as sess:
        assert 'user_id' not in sess and 'pin_token' not in sess


def test_lockout_is_stored_in_the_database(app, app_ctx):
    from models import PinAttempt
    sign_in(app, 'Phoenix', '1234')
    for _ in range(5):
        sign_in(app, 'Phoenix', '0001')
    # Kept in the database, so restarting the app doesn't lift it
    assert db.session.get(PinAttempt, 'phoenix').failures == 5


def test_lockout_expires_after_five_minutes(app, app_ctx):
    from datetime import timedelta
    from models import PinAttempt, utcnow
    sign_in(app, 'Phoenix', '1234')
    for _ in range(5):
        sign_in(app, 'Phoenix', '0001')
    db.session.get(PinAttempt, 'phoenix').first_failure = utcnow() - timedelta(minutes=6)
    db.session.commit()
    assert sign_in(app, 'Phoenix', '1234')[1] == 'Phoenix'
    assert db.session.get(PinAttempt, 'phoenix') is None


def guess_a_day_of_pins(app):
    """Wrong PINs in 5-minute bursts until the daily limit, each burst after the short lockout has passed."""
    from datetime import timedelta
    from app import PIN_DAILY_LIMIT, MAX_PIN_ATTEMPTS
    from models import PinAttempt, utcnow
    for guess in range(PIN_DAILY_LIMIT):
        if guess % MAX_PIN_ATTEMPTS == 0 and db.session.get(PinAttempt, 'phoenix'):
            db.session.get(PinAttempt, 'phoenix').first_failure = utcnow() - timedelta(minutes=6)
            db.session.commit()
        sign_in(app, 'Phoenix', '0001')
    db.session.get(PinAttempt, 'phoenix').first_failure = utcnow() - timedelta(minutes=6)
    db.session.commit()


def test_a_day_of_wrong_pins_locks_the_name_until_tomorrow(app, app_ctx):
    """5 guesses every 5 minutes would find any PIN in about two days, so there's a daily limit too."""
    sign_in(app, 'Phoenix', '1234')
    guess_a_day_of_pins(app)
    client = app.test_client()
    client.post('/setup', data={'username': 'Phoenix', 'pin': '1234'})
    assert signed_in_name(client) is None  # locked even with the right PIN
    assert 'try again tomorrow' in client.get('/').get_data(as_text=True)


def test_daily_lock_ends_after_a_day(app, app_ctx):
    from datetime import timedelta
    from models import RateLimit, utcnow
    sign_in(app, 'Phoenix', '1234')
    guess_a_day_of_pins(app)
    db.session.get(RateLimit, 'pin-day:phoenix').window_start = utcnow() - timedelta(days=1, minutes=1)
    db.session.commit()
    assert sign_in(app, 'Phoenix', '1234')[1] == 'Phoenix'
    assert db.session.get(RateLimit, 'pin-day:phoenix') is None  # the right PIN clears the day's count


def test_short_lockout_guesses_dont_use_up_the_day(app, app_ctx):
    from models import RateLimit
    sign_in(app, 'Phoenix', '1234')
    for _ in range(30):
        sign_in(app, 'Phoenix', '0001')  # only the first 5 are checked, the rest hit the 5-minute lockout
    assert db.session.get(RateLimit, 'pin-day:phoenix').hits == 5


def test_right_pin_clears_earlier_wrong_guesses(app, app_ctx):
    from models import PinAttempt
    sign_in(app, 'Phoenix', '1234')
    sign_in(app, 'Phoenix', '0001')
    sign_in(app, 'Phoenix', '1234')
    assert db.session.get(PinAttempt, 'phoenix') is None
