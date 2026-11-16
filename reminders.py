"""Daily-challenge reminders: who gets one and which push addresses are accepted (pure rules, no database).

Players switch reminders on from the daily page; the browser gives a push "subscription" (an address at the
browser maker's push service plus two keys), which /push/subscribe stores. Once a day Vercel's cron calls
/cron/daily-reminder, which sends one notification to every subscribed player who hasn't finished today's challenge.
Sending goes through pywebpush, signed with the site's VAPID key pair (make_vapid_keys.py).
"""
from urllib.parse import urlparse

MAX_PER_PLAYER = 5  # Devices one player can get reminders on; the oldest is dropped beyond that

# The push services of Chrome/Edge/Android, Firefox, Windows and Safari/iOS. The server sends a request to whatever
# address a subscription names, so only these are accepted, never an address anyone could make up.
PUSH_HOSTS = ('fcm.googleapis.com', 'updates.push.services.mozilla.com', 'push.services.mozilla.com',
              'web.push.apple.com')
PUSH_HOST_SUFFIXES = ('.notify.windows.com', '.push.apple.com')


def valid_endpoint(endpoint):
    """Whether `endpoint` is an https address at a known push service."""
    if not isinstance(endpoint, str) or len(endpoint) > 500:
        return False
    url = urlparse(endpoint)
    host = (url.hostname or '').lower()
    return url.scheme == 'https' and (host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES))


def due(subscriptions, finished_today, today):
    """The subscriptions to remind now: their player hasn't finished today's challenge, and they weren't reminded
    today already (so a second call on the same day sends nothing).

    subscriptions: objects with user_id and last_sent ('YYYY-MM-DD' or None). finished_today: set of user ids.
    """
    return [s for s in subscriptions if s.user_id not in finished_today and s.last_sent != today]


def gone(status_code):
    """Whether a push service's answer means the subscription no longer exists (the player turned notifications
    off, or uninstalled), so it should be deleted rather than tried again tomorrow."""
    return status_code in (404, 410)
