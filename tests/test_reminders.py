"""Daily-challenge reminders: switching them on, who gets one, and what happens when a device is gone."""
import json
from types import SimpleNamespace

import pytest
from pywebpush import WebPushException

import app as app_module
import reminders
from views import timers
from app import db
from make_vapid_keys import make_keys
from models import DailyChallenge, PushSubscription, QuizSession, User

SECRET = 'test-cron-secret'
FCM = 'https://fcm.googleapis.com/fcm/send/abc'


# ---------- Rules (reminders.py) ----------------------------------------------------------------------------------

@pytest.mark.parametrize('endpoint, ok', [
    (FCM, True),
    ('https://updates.push.services.mozilla.com/wpush/v2/x', True),
    ('https://web.push.apple.com/QAbc', True),
    ('https://db5p.notify.windows.com/w/?token=x', True),
    ('http://fcm.googleapis.com/fcm/send/abc', False),        # not https
    ('https://evil.example/fcm.googleapis.com', False),       # not the push service
    ('https://fcm.googleapis.com.evil.example/x', False),
    ('https://localhost/x', False),
    (None, False),
    ('https://fcm.googleapis.com/' + 'x' * 500, False),
])
def test_only_real_push_services_are_accepted(endpoint, ok):
    assert reminders.valid_endpoint(endpoint) is ok


def test_due_skips_players_who_played_and_devices_already_reminded():
    subs = [SimpleNamespace(user_id=1, last_sent=None), SimpleNamespace(user_id=2, last_sent=None),
            SimpleNamespace(user_id=3, last_sent='2026-11-16'), SimpleNamespace(user_id=4, last_sent='2026-11-15')]
    assert [s.user_id for s in reminders.due(subs, {2}, '2026-11-16')] == [1, 4]


def test_gone_means_deleted():
    assert reminders.gone(410) and reminders.gone(404) and not reminders.gone(500) and not reminders.gone(429)


# ---------- Switching them on and off -----------------------------------------------------------------------------

@pytest.fixture
def keys(app, monkeypatch):
    public, private = make_keys()
    monkeypatch.setitem(app.config, 'VAPID_PUBLIC_KEY', public)
    monkeypatch.setitem(app.config, 'VAPID_PRIVATE_KEY', private)
    monkeypatch.setitem(app.config, 'CRON_SECRET', SECRET)
    return public


def subscribe(client, endpoint=FCM):
    return client.post('/push/subscribe', json={'endpoint': endpoint, 'keys': {'p256dh': 'BKey', 'auth': 'auth'}})


def test_the_daily_page_offers_reminders_only_with_keys(app, login, monkeypatch):
    monkeypatch.setitem(app.config, 'VAPID_PUBLIC_KEY', '')
    assert 'data-push ' not in login('Ann').get('/daily').get_data(as_text=True)
    public, private = make_keys()
    monkeypatch.setitem(app.config, 'VAPID_PUBLIC_KEY', public)
    monkeypatch.setitem(app.config, 'VAPID_PRIVATE_KEY', private)
    html = login('Ann').get('/daily').get_data(as_text=True)
    assert f'data-key="{public}"' in html and 'Remind me every day' in html
    assert 'data-push ' not in app.test_client().get('/daily').get_data(as_text=True)  # Signed out


def test_subscribing_stores_the_device_with_the_language(keys, login, app_ctx):
    assert subscribe(login('Ann', language='de')).get_json() == {'ok': True}
    row = PushSubscription.query.one()
    assert (row.endpoint, row.p256dh, row.auth, row.language) == (FCM, 'BKey', 'auth', 'de')


def test_bad_subscriptions_are_refused(keys, login, app_ctx):
    client = login('Ann')
    assert subscribe(client, 'https://evil.example/hook').status_code == 400
    assert client.post('/push/subscribe', json={'endpoint': FCM}).status_code == 400
    assert PushSubscription.query.count() == 0


def test_signed_out_or_without_keys_there_is_nothing_to_subscribe_to(app, login, keys, monkeypatch):
    assert subscribe(app.test_client()).status_code == 404
    monkeypatch.setitem(app.config, 'VAPID_PRIVATE_KEY', '')
    assert subscribe(login('Ann')).status_code == 404


def test_a_device_belongs_to_whoever_switched_it_on_last(keys, login, app_ctx):
    subscribe(login('Ann'))
    subscribe(login('Ben'))
    assert PushSubscription.query.one().user_id == User.query.filter_by(username='Ben').one().id


def test_a_player_keeps_at_most_a_few_devices(keys, login, app_ctx):
    client = login('Ann')
    for i in range(reminders.MAX_PER_PLAYER + 2):
        subscribe(client, f'{FCM}{i}')
    endpoints = [s.endpoint for s in PushSubscription.query.order_by(PushSubscription.id)]
    assert endpoints == [f'{FCM}{i}' for i in range(2, reminders.MAX_PER_PLAYER + 2)]


def test_unsubscribing_only_removes_your_own_device(keys, login, app_ctx):
    subscribe(login('Ann'))
    login('Ben').post('/push/unsubscribe', json={'endpoint': FCM})
    assert PushSubscription.query.count() == 1
    login('Ann').post('/push/unsubscribe', json={'endpoint': FCM})
    assert PushSubscription.query.count() == 0


def test_deleting_a_player_deletes_their_devices(keys, login, app_ctx):
    subscribe(login('Ann'))
    app_module.delete_player_data(User.query.filter_by(username='Ann').one())
    assert PushSubscription.query.count() == 0


# ---------- Sending -----------------------------------------------------------------------------------------------

@pytest.fixture
def sent(monkeypatch):
    """Pushes 'sent' through pywebpush, as (endpoint, message); set .fail to {endpoint: status} to make some fail."""
    class Log(list):
        fail = {}
    log = Log()

    def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims, ttl, timeout):
        endpoint = subscription_info['endpoint']
        assert vapid_claims == {'sub': 'https://www.wikifakefacts.com'} and subscription_info['keys']['auth']
        if endpoint in log.fail:
            raise WebPushException('push failed', response=SimpleNamespace(status_code=log.fail[endpoint]))
        log.append((endpoint, json.loads(data)))
    monkeypatch.setattr(timers, 'webpush', fake_webpush)
    return log


def cron(app):
    return app.test_client().get('/cron/daily-reminder', headers={'Authorization': f'Bearer {SECRET}'})


def finish_daily(name):
    user = User.query.filter_by(username=name).one()
    challenge = DailyChallenge.query.filter_by(date=app_module.today_utc()).first() or DailyChallenge(
        date=app_module.today_utc(), language='English', topic='Cat', questions_json='[]', explanations_json='[]')
    db.session.add(challenge)
    db.session.commit()
    db.session.add(QuizSession(user_id=user.id, topic='Cat', questions_json='[]', total_questions=0,
                               daily_challenge_id=challenge.id, is_completed=True))
    db.session.commit()


def test_the_reminder_needs_the_cron_secret(app, keys, sent):
    assert app.test_client().get('/cron/daily-reminder').status_code == 401
    assert sent == []


def test_players_who_havent_played_get_one_reminder_in_their_language(app, keys, login, sent, app_ctx):
    subscribe(login('Ann', language='de'), f'{FCM}ann')
    subscribe(login('Ben'), f'{FCM}ben')
    finish_daily('Ben')
    assert cron(app).get_json() == {'sent': 1, 'removed': 0, 'failed': 0}
    (endpoint, message), = sent
    topic = app_module.daily_topic(app_module.today_utc())
    assert endpoint == f'{FCM}ann' and message['url'] == '/daily'
    assert message['title'] == 'Die heutige Challenge ist da' and message['body'].startswith(f'{topic}: ')
    assert cron(app).get_json()['sent'] == 0  # Not twice on the same day


def test_gone_devices_are_deleted_and_other_errors_kept(app, keys, login, sent, app_ctx):
    subscribe(login('Ann'), f'{FCM}gone')
    subscribe(login('Ben'), f'{FCM}busy')
    sent.fail = {f'{FCM}gone': 410, f'{FCM}busy': 503}
    assert cron(app).get_json() == {'sent': 0, 'removed': 1, 'failed': 1}
    assert [s.endpoint for s in PushSubscription.query] == [f'{FCM}busy']
    assert PushSubscription.query.one().last_sent is None  # Tried again tomorrow


def test_the_real_signing_works_with_the_generated_keys(app, keys, monkeypatch, app_ctx):
    """send_push hands pywebpush a key it can sign with (the request itself is stopped before it leaves)."""
    captured = {}

    def fake_post(url, data, headers, timeout, **kwargs):
        captured.update(url=url, headers=headers)
        return SimpleNamespace(status_code=201, text='', headers={})
    import pywebpush
    monkeypatch.setattr(pywebpush.requests, 'post', fake_post)
    browser_key = make_keys()[0]  # Any valid P-256 public key stands in for the browser's
    subscription = SimpleNamespace(endpoint=FCM, p256dh=browser_key, auth='c2VjcmV0c2VjcmV0MTIzNA')
    assert timers.send_push(subscription, {'title': 'Hi'}) is None
    assert captured['url'] == FCM and captured['headers']['Authorization'].startswith('vapid t=')
