"""Finn's wardrobe: badges unlock items, a player's Finn wears their choice everywhere."""
import outfits
from app import db
from models import User, UserBadge


# ---------- Rules (outfits.py) ------------------------------------------------------------------------------------

def test_defaults_are_always_unlocked_and_the_rest_need_their_badge():
    assert outfits.unlocked('hat', 'classic', set())
    assert not outfits.unlocked('hat', 'navy', set()) and outfits.unlocked('hat', 'navy', {'quiz_10'})
    assert not outfits.unlocked('hat', 'top hat', {'quiz_10'}) and not outfits.unlocked('shoes', 'red', set())


def test_every_item_is_unlocked_by_a_real_badge():
    from badges import BADGES_BY_KEY
    for slot, items in outfits.SLOTS.items():
        assert items[0][2] is None, f'the first {slot} must be free'
        for key, name, badge, colours in items[1:]:
            assert badge in BADGES_BY_KEY, f'{slot}/{key} needs the unknown badge {badge}'


def test_parse_keeps_known_items_and_falls_back_to_defaults():
    assert outfits.parse('hat=navy;scarf=blue;extra=glasses') == {'hat': 'navy', 'scarf': 'blue', 'coat': 'brown',
                                                                 'extra': 'glasses'}
    assert outfits.parse('hat=crown;nonsense;coat=') == outfits.DEFAULTS
    assert outfits.parse(None) == outfits.DEFAULTS


def test_allowed_drops_what_isnt_unlocked():
    choice = {'hat': 'gold', 'scarf': 'blue', 'coat': 'grey', 'extra': 'star'}
    assert outfits.allowed(choice, {'daily', 'hard_ace'}) == {'hat': 'classic', 'scarf': 'blue', 'coat': 'grey',
                                                              'extra': 'none'}


def test_serialize_stores_only_what_differs():
    assert outfits.serialize(outfits.DEFAULTS) == ''
    assert outfits.serialize({**outfits.DEFAULTS, 'scarf': 'blue', 'extra': 'star'}) == 'scarf=blue;extra=star'
    assert outfits.parse(outfits.serialize({**outfits.DEFAULTS, 'hat': 'green'}))['hat'] == 'green'


def test_style_and_extra():
    assert outfits.style(outfits.DEFAULTS) == '' and outfits.extra(outfits.DEFAULTS) == ''
    look = outfits.parse('hat=navy;extra=bowtie')
    assert '--finn-hat:#27407a' in outfits.style(look) and outfits.extra(look) == 'bowtie'


# ---------- In the app --------------------------------------------------------------------------------------------

def give(name, *badges):
    user = User.query.filter_by(username=name).one()
    for badge in badges:
        db.session.add(UserBadge(user_id=user.id, badge=badge))
    db.session.commit()
    return user


def test_the_wardrobe_shows_what_is_locked_and_why(login, app_ctx):
    client = login('Ann')
    give('Ann', 'daily')
    html = client.get('/account').get_data(as_text=True)
    assert "Finn&#39;s wardrobe" in html or "Finn's wardrobe" in html
    blue = html.split('value="blue"')[1].split('</label>')[0]
    navy = html.split('value="navy"')[1].split('</label>')[0]
    assert 'disabled' not in blue and 'Earn the badge' not in blue
    assert 'disabled' in navy and 'Quiz regular' in navy


def test_saving_dresses_finn_everywhere(login, app_ctx):
    client = login('Ann')
    user = give('Ann', 'daily', 'comeback')
    response = client.post('/account/finn', data={'hat': 'classic', 'scarf': 'blue', 'coat': 'brown', 'extra': 'glasses'})
    assert response.headers['Location'].endswith('/account#finn')
    db.session.refresh(user)
    assert user.finn_outfit == 'scarf=blue;extra=glasses'
    for page in ('/setup', '/history', '/account'):
        html = client.get(page).get_data(as_text=True)
        assert '--finn-scarf:#2f6fd6' in html and 'extra-glasses' in html, page


def test_locked_items_cant_be_saved(login, app_ctx):
    client = login('Ann')
    user = give('Ann')
    client.post('/account/finn', data={'hat': 'gold', 'scarf': 'yellow', 'coat': 'midnight', 'extra': 'star'})
    db.session.refresh(user)
    assert user.finn_outfit is None


def test_other_players_and_guests_see_the_classic_finn(login, app, app_ctx):
    ann = login('Ann')
    give('Ann', 'daily')
    ann.post('/account/finn', data={'scarf': 'blue'})
    assert '--finn-scarf' not in login('Ben').get('/setup').get_data(as_text=True)
    assert '--finn-scarf' not in app.test_client().get('/').get_data(as_text=True)


def test_signed_out_cant_save(app):
    assert app.test_client().post('/account/finn', data={'scarf': 'blue'}).headers['Location'].endswith('/')
