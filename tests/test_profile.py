"""Player profiles: photo, favorite topic, changing name, and the public profile page."""
import io

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

import app as app_module
from app import db
from conftest import answers, play_quiz, sign_in_as, signed_in_name
from models import Score, User


def picture(fmt='PNG', size=(400, 300), exif=None):
    output = io.BytesIO()
    Image.new('RGB', size, (200, 40, 40)).save(output, fmt, **({'exif': exif} if exif else {}))
    output.seek(0)
    return output


def upload(client, data, filename='me.png'):
    return client.post('/account/photo', data={'photo': (data, filename)}, content_type='multipart/form-data')


@pytest.fixture
def player(login, app_ctx):
    """'Player' with PIN 1234 and one finished quiz."""
    client = login('Player')
    user = User.query.filter_by(username='Player').one()
    user.pin_hash = generate_password_hash('1234')
    db.session.commit()
    sign_in_as(client, 'Player')  # Setting the PIN ended the session, as changing it does in the app
    play_quiz(client, answers(3, 3))
    return client


def get_player(name='Player'):
    db.session.expire_all()
    return User.query.filter_by(username=name).first()


# ---------- Photo ----------------------------------------------------------------------------------------------

def test_photo_is_cropped_to_a_square_webp_and_served(player):
    upload(player, picture(size=(800, 400)))
    user = get_player()
    image = Image.open(io.BytesIO(user.avatar))
    assert (image.format, image.size) == ('WEBP', (256, 256))

    response = player.get(f'/players/{user.id}/avatar?v=1')
    assert response.status_code == 200 and response.mimetype == 'image/webp'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.cache_control.max_age >= 24 * 3600
    assert response.cache_control.private and not response.cache_control.public  # Never kept by a CDN


def test_photo_metadata_is_dropped(player):
    exif = Image.Exif()
    exif[0x010F] = 'SecretCam'  # Camera make
    upload(player, picture('JPEG', exif=exif.tobytes()), 'me.jpg')
    assert b'SecretCam' not in get_player().avatar


def test_photo_shows_on_pages(player):
    upload(player, picture())
    user_id = get_player().id
    for page in ('/players', f'/players/{user_id}', '/account', '/leaderboard'):
        assert f'/players/{user_id}/avatar?v=' in player.get(page).get_data(as_text=True), page


@pytest.mark.parametrize('data, filename', [
    (io.BytesIO(b'just some text'), 'me.png'),
    (io.BytesIO(b'%PDF-1.4 not a picture'), 'me.pdf'),
])
def test_non_pictures_are_refused(player, data, filename):
    upload(player, data, filename)
    assert get_player().avatar_updated_at is None
    assert "isn&#39;t a picture we can use" in player.get('/account').get_data(as_text=True)


def test_huge_pictures_are_refused(player, monkeypatch):
    monkeypatch.setattr(app_module, 'AVATAR_MAX_PIXELS', 1000)
    upload(player, picture(size=(100, 100)))
    assert get_player().avatar_updated_at is None


def test_remove_photo(player):
    upload(player, picture())
    user_id = get_player().id
    player.post('/account/photo', data={'remove': '1'})
    assert get_player().avatar is None and get_player().avatar_updated_at is None
    assert player.get(f'/players/{user_id}/avatar').status_code == 404


def test_upload_without_a_file(player):
    upload(player, io.BytesIO(b''), '')
    assert 'Please choose a picture' in player.get('/account').get_data(as_text=True)


# ---------- Favorite topic -------------------------------------------------------------------------------------

def test_favorite_topic_is_saved_and_cleared(player):
    player.post('/account/topic', data={'topic': 'Animals'})
    assert get_player().favorite_topic == 'Animals'
    assert 'Favorite topic: Animals' in player.get(f'/players/{get_player().id}').get_data(as_text=True)
    player.post('/account/topic', data={'topic': ''})
    assert get_player().favorite_topic is None


def test_unknown_favorite_topic_is_ignored(player):
    player.post('/account/topic', data={'topic': 'Animals'})
    player.post('/account/topic', data={'topic': '<script>'})
    assert get_player().favorite_topic == 'Animals'


# ---------- Changing name --------------------------------------------------------------------------------------

def test_rename_keeps_scores_and_sign_in(player):
    user_id = get_player().id
    player.post('/account/name', data={'new_name': 'Phoenix', 'pin': '1234'})
    assert get_player('Player') is None
    assert get_player('Phoenix').id == user_id
    assert Score.query.filter_by(user_id=user_id).count() == 1
    assert signed_in_name(player) == 'Phoenix'
    assert player.get('/account').status_code == 200


def test_rename_needs_the_pin(player):
    player.post('/account/name', data={'new_name': 'Phoenix', 'pin': '0000'})
    assert get_player('Phoenix') is None and get_player('Player')


def test_wrong_pins_on_rename_count_toward_lockout(player):
    for _ in range(5):
        player.post('/account/name', data={'new_name': 'Phoenix', 'pin': '0000'})
    player.post('/account/name', data={'new_name': 'Phoenix', 'pin': '1234'})
    assert get_player('Phoenix') is None


def test_cannot_take_another_players_name(player, make_user):
    make_user('Phoenix')
    player.post('/account/name', data={'new_name': 'PHOENIX', 'pin': '1234'})
    assert get_player('Player') and get_player('PHOENIX') is None
    assert 'already taken' in player.get('/account').get_data(as_text=True)


def test_can_change_the_case_of_own_name(player):
    player.post('/account/name', data={'new_name': 'PLAYER', 'pin': '1234'})
    assert get_player('PLAYER')


@pytest.mark.parametrize('name', ['ab', 'x' * 31])
def test_new_name_length_is_checked(player, name):
    player.post('/account/name', data={'new_name': name, 'pin': '1234'})
    assert get_player('Player')


def test_setup_refuses_long_new_names_but_not_existing_ones(app, make_user, app_ctx):
    client = app.test_client()
    client.post('/setup', data={'username': 'y' * 31, 'pin': '1234'})
    assert User.query.count() == 0
    make_user('z' * 40)  # Signed up before the limit
    client.post('/setup', data={'username': 'z' * 40, 'pin': '1234'})
    assert signed_in_name(client) == 'z' * 40


# ---------- Public profile -------------------------------------------------------------------------------------

def test_profile_is_public_and_shows_stats(player, app):
    user_id = get_player().id
    html = app.test_client().get(f'/players/{user_id}').get_data(as_text=True)
    assert 'text-break">Player' in html
    assert '100%' in html and '1 / ' in html  # average score and badge count
    assert 'Edit profile' not in html and '/analysis' not in html.split('<main')[1]


def test_own_profile_links_to_editing_and_analysis(player):
    html = player.get(f'/players/{get_player().id}').get_data(as_text=True)
    assert 'Edit profile' in html and 'href="/analysis"' in html


def test_profile_of_player_without_games(login, app_ctx):
    client = login('Newbie')
    html = client.get(f'/players/{get_player("Newbie").id}').get_data(as_text=True)
    assert 'No badges yet.' in html


def test_unknown_profile_is_404(app):
    assert app.test_client().get('/players/999').status_code == 404
    assert app.test_client().get('/players/999/avatar').status_code == 404


def test_players_page_links_to_profiles(player):
    assert f'href="/players/{get_player().id}"' in player.get('/players').get_data(as_text=True)
