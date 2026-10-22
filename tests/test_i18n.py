"""Interface translations: language switching, rendering in the chosen language, and catalog health."""
import json
import re
from datetime import datetime

import pytest

from translations import CATALOGS, UI_LANGUAGES, find_missing, format_date, translate, translate_plural

PLACEHOLDER = re.compile(r'\{\w+\}')


def test_language_switch_sets_session_and_returns_to_page(app):
    client = app.test_client()
    response = client.get('/language/de?next=/leaderboard')
    assert response.headers['Location'] == '/leaderboard'
    with client.session_transaction() as sess:
        assert sess['language'] == 'de'


@pytest.mark.parametrize('target', ['//evil.example', 'https://evil.example', 'evil', '/%09/evil.example', '/%5Cevil.example',
                                    '/%0A/evil.example'])
def test_language_switch_never_redirects_off_site(app, target):
    assert app.test_client().get(f'/language/de?next={target}').headers['Location'] == '/'


def test_unknown_language_is_ignored(app):
    client = app.test_client()
    client.get('/language/xx')
    with client.session_transaction() as sess:
        assert 'language' not in sess


def test_arabic_pages_are_right_to_left(login):
    html = login('Player', language='ar').get('/leaderboard').get_data(as_text=True)
    assert '<html lang="ar" dir="rtl"' in html
    assert 'bootstrap.rtl.min.css' in html and 'family=Cairo' in html
    assert f'>{translate("Leaderboard", "ar")}<' in html


def test_other_languages_stay_left_to_right(login):
    html = login('Player', language='de').get('/leaderboard').get_data(as_text=True)
    assert '<html lang="de" dir="ltr"' in html
    assert 'bootstrap.min.css' in html and 'bootstrap.rtl' not in html and 'family=Cairo' not in html


def test_arabic_is_offered_for_quizzes(login):
    html = login('Player', language='ar').get('/setup').get_data(as_text=True)
    assert '<option value="ar" selected>' in html


def test_pages_render_in_chosen_language(login):
    html = login('Player', language='de').get('/leaderboard').get_data(as_text=True)
    assert '<html lang="de"' in html
    assert f'>{translate("Leaderboard", "de")}<' in html
    assert translate('Leaderboard', 'de') != 'Leaderboard'


def test_flash_messages_are_translated(app):
    client = app.test_client()
    client.get('/language/fr')
    client.post('/setup', data={'username': 'Player', 'pin': '12'})
    html = client.get('/').get_data(as_text=True)
    assert translate('Please enter a 4-digit PIN.', 'fr') in html


def test_quiz_language_defaults_to_interface_language(login):
    html = login('Player', language='it').get('/setup').get_data(as_text=True)
    assert '<option value="it" selected>' in html


def test_script_strings_are_valid_json(login):
    html = login('Player', language='pt').get('/').get_data(as_text=True)
    i18n = json.loads(re.search(r'window\.WF_I18N = (\{.*?\});', html, re.S).group(1))
    assert len(i18n['tips']['quiz']) == 5 and i18n['tips']['quiz'][0] != 'Reading up on your topic…'


def test_plurals_and_dates():
    assert translate_plural('{n} game', '{n} games', 1, 'en') == '1 game'
    assert translate_plural('{n} game', '{n} games', 3, 'en') == '3 games'
    moment = datetime(2026, 10, 21, 14, 5)
    assert format_date(moment, lang='en') == 'Oct 21, 2026'
    assert format_date(moment, with_time=True, lang='en') == 'Oct 21, 2026 · 14:05'
    assert format_date(moment, lang='de') == f'21 {translate("Oct", "de")} 2026'


def test_missing_strings_fall_back_to_english():
    assert translate('A string nobody translated', 'de') == 'A string nobody translated'


# ---------- Catalog health -------------------------------------------------------------------------------------

def test_every_used_string_is_translated():
    missing, _used = find_missing()
    assert {lang: strings for lang, strings in missing.items() if strings} == {}


@pytest.mark.parametrize('lang', UI_LANGUAGES[1:])
def test_catalog_entries_are_safe_and_keep_placeholders(lang):
    for english, translated in CATALOGS[lang].items():
        assert translated.strip(), f'{lang}: empty translation for {english!r}'
        assert sorted(PLACEHOLDER.findall(english)) == sorted(PLACEHOLDER.findall(translated)), \
            f'{lang}: placeholders differ in {english!r} -> {translated!r}'
        # Catalog text is inserted into HTML unescaped
        assert not re.search(r'["<>]|&\w+;', translated), f'{lang}: HTML-unsafe text in {translated!r}'


def test_catalogs_have_no_unused_keys():
    _missing, used = find_missing()
    stale = {lang: sorted(set(catalog) - set(used)) for lang, catalog in CATALOGS.items()}
    assert {lang: keys for lang, keys in stale.items() if keys} == {}
