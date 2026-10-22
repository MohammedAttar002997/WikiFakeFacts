"""Sign-ins belong to an account, not a name, and PIN guessing is counted before each check."""
import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from app import db, pin_attempt_allowed
from conftest import sign_in_as, signed_in_name
from models import PinAttempt, User


@pytest.fixture
def phoenix(app, app_ctx):
    """'Phoenix' with PIN 1234, signed in on two devices."""
    db.session.add(User(username='Phoenix', pin_hash=generate_password_hash('1234')))
    db.session.commit()
    laptop, phone = app.test_client(), app.test_client()
    sign_in_as(laptop, 'Phoenix')
    sign_in_as(phone, 'Phoenix')
    return laptop, phone


def test_deleting_an_account_signs_out_its_other_devices(phoenix, app):
    laptop, phone = phoenix
    laptop.post('/account/delete', data={'pin': '1234', 'confirm': '1'})
    # Someone else takes the freed name; the phone must not end up in their account
    app.test_client().post('/setup', data={'username': 'Phoenix', 'pin': '9999'})
    assert signed_in_name(phone) is None
    assert phone.get('/account').headers['Location'] == '/'


def test_a_signed_out_device_cannot_recreate_a_deleted_player(phoenix):
    laptop, phone = phoenix
    laptop.post('/account/delete', data={'pin': '1234', 'confirm': '1'})
    phone.post('/custom_quiz', data={'length': '1', 'difficulty': '2', 'language': 'en',
                                     'url': 'https://example.com/v'})
    assert User.query.count() == 0  # It used to come back without a PIN, claimable by anyone


def test_changing_the_pin_signs_out_other_devices_only(phoenix):
    laptop, phone = phoenix
    laptop.post('/account/pin', data={'current_pin': '1234', 'new_pin': '5678'})
    assert signed_in_name(laptop) == 'Phoenix'
    assert signed_in_name(phone) is None


def test_renaming_keeps_every_device_signed_in(phoenix):
    laptop, phone = phoenix
    laptop.post('/account/name', data={'new_name': 'Firebird', 'pin': '1234'})
    assert signed_in_name(phone) == 'Firebird'
    assert 'Firebird' in phone.get('/setup').get_data(as_text=True)


def test_sessions_from_before_this_change_are_signed_out(app, make_user):
    make_user('Phoenix')
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'Phoenix'  # How sessions named the player before
    assert client.get('/account').headers['Location'] == '/'
    with client.session_transaction() as sess:
        assert 'username' not in sess


def test_tampered_session_is_rejected(phoenix):
    laptop, _phone = phoenix
    with laptop.session_transaction() as sess:
        sess['pin_token'] = 'not-the-token'
    assert signed_in_name(laptop) is None
    assert laptop.get('/account').headers['Location'] == '/'


# ---------- PIN attempts ----------------------------------------------------------------------------------------

def test_attempts_are_counted_before_the_pin_is_checked(phoenix, app, monkeypatch):
    """Counting first is what stops parallel guesses: each one is on the books before its slow check runs."""
    counts_seen = []
    real_check = app_module.check_password_hash

    def checking(pin_hash, pin):
        counts_seen.append(db.session.get(PinAttempt, 'phoenix').failures)
        return real_check(pin_hash, pin)

    monkeypatch.setattr(app_module, 'check_password_hash', checking)
    client = app.test_client()
    for _ in range(3):
        client.post('/setup', data={'username': 'Phoenix', 'pin': '0000'})
    assert counts_seen == [1, 2, 3]


def test_sixth_attempt_is_refused_even_with_the_right_pin(phoenix, app):
    client = app.test_client()
    for _ in range(5):
        client.post('/setup', data={'username': 'Phoenix', 'pin': '0000'})
    client.post('/setup', data={'username': 'Phoenix', 'pin': '1234'})
    assert signed_in_name(client) is None


def test_differently_written_names_share_one_count(phoenix, app):
    client = app.test_client()
    for name in ('phoenix', 'PHOENIX', ' Phoenix ', 'pHoEnIx', 'Phoenix'):
        client.post('/setup', data={'username': name, 'pin': '0000'})
    client.post('/setup', data={'username': 'Phoenix', 'pin': '1234'})
    assert signed_in_name(client) is None


def test_right_pin_clears_the_count(phoenix, app):
    client = app.test_client()
    for _ in range(4):
        client.post('/setup', data={'username': 'Phoenix', 'pin': '0000'})
    client.post('/setup', data={'username': 'Phoenix', 'pin': '1234'})
    assert signed_in_name(client) == 'Phoenix'
    assert db.session.get(PinAttempt, 'phoenix') is None


def test_counting_a_new_name_twice_needs_no_existing_row(app_ctx):
    # Two first attempts used to race on inserting the row and fail with an IntegrityError
    assert pin_attempt_allowed('newname') and pin_attempt_allowed('newname')
    assert db.session.get(PinAttempt, 'newname').failures == 2
