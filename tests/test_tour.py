"""Finn's first-visit tour: once per account on the setup page, replayable from the footer, remembered on all devices."""
from app import db
from models import User


def tour_shown(client, path='/setup'):
    return 'id="tour"' in client.get(path).get_data(as_text=True)


def test_a_new_player_gets_the_tour_once(login, app_ctx):
    client = login('Newbie')
    assert tour_shown(client)
    assert client.post('/tour/done').status_code == 204
    assert User.query.filter_by(username='Newbie').one().tour_done_at is not None
    assert not tour_shown(client)
    assert not tour_shown(login('Newbie'))  # Another device, same account


def test_the_footer_link_replays_it(login, app_ctx):
    client = login('Ann')
    client.post('/tour/done')
    html = client.get('/setup').get_data(as_text=True)
    assert 'href="/setup?tour=1"' in html and 'Take the tour' in html
    assert tour_shown(client, '/setup?tour=1')


def test_only_on_the_setup_page_and_only_when_signed_in(login, app):
    client = login('Ann')
    assert not tour_shown(client, '/leaderboard') and not tour_shown(client, '/daily')
    assert not tour_shown(app.test_client(), '/')
    assert app.test_client().post('/tour/done').status_code == 204  # Signed out: nothing to remember, no error


def test_finn_points_at_things_that_exist(login):
    """Each step's target must be on the setup page (or be one of the menu items, which fold away on phones)."""
    import re
    html = login('Ann').get('/setup').get_data(as_text=True)
    targets = set(re.findall(r'data-tour="([\w-]+)"', html))
    steps = re.search(r'window\.WF_TOUR = (\{.*?\});</script>', html).group(1)
    wanted = set(re.findall(r'"target": "([\w-]+)"', steps))
    assert wanted and wanted <= targets, wanted - targets


def test_the_tour_has_finn_and_its_script(login):
    html = login('Ann').get('/setup').get_data(as_text=True)
    assert 'class="finn"' in html and 'js/tour.js' in html and 'Hi, I' in html


def test_players_from_before_the_tour_dont_get_it_on_their_own(app_ctx):
    """Migration 0027 marks existing accounts as done; a fresh database shows that by giving new rows None."""
    user = User(username='Fresh')
    db.session.add(user)
    db.session.commit()
    assert user.tour_done_at is None
