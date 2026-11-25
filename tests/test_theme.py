"""Themes: every page picks its look with <html data-theme> and the theme's own stylesheet (CLAUDE.md, "Themes")."""
import os
import re

import app as app_module

THEME = 'casefile'
CSS = f'/static/css/theme-{THEME}.css'
STATIC = os.path.join(os.path.dirname(app_module.__file__), 'public', 'static')


def test_pages_use_the_case_file_theme_after_the_base_styles(app):
    html = app.test_client().get('/').get_data(as_text=True)
    assert f'data-theme="{THEME}"' in html and 'data-bs-theme="light"' in html
    assert html.index('/static/css/app.css') < html.index(CSS)  # The theme overrides the base styles
    assert app.test_client().get(CSS).status_code == 200


def test_the_theme_has_light_and_dark(app):
    with open(os.path.join(STATIC, 'css', f'theme-{THEME}.css'), encoding='utf-8') as f:
        css = f.read()
    assert f'[data-theme="{THEME}"][data-bs-theme="dark"]' in css
    for token in ('--wf-bg', '--wf-surface', '--wf-text', '--wf-brand', '--wf-on-brand', '--wf-marker'):
        light, dark = (css.split(f'[data-theme="{THEME}"][data-bs-theme="dark"] {{')[i] for i in (0, 1))
        assert token + ':' in light and token + ':' in dark.split('}')[0], token


def test_the_widget_and_offline_copy_use_the_theme_too(app, app_ctx):
    html = app.test_client().get('/embed/daily').get_data(as_text=True)
    assert f'data-theme="{THEME}"' in html and CSS in html
    sw = app.test_client().get('/sw.js').get_data(as_text=True)
    assert CSS in re.search(r'const PRECACHE = \[(.*?)\];', sw, re.S).group(1)


def test_the_theme_fonts_are_loaded(app):
    html = app.test_client().get('/').get_data(as_text=True)
    assert all(font in html for font in ('Courier+Prime', 'Fraunces', 'Special+Elite'))


# ---------- The theme's own words (themes.py) -------------------------------------------------------------------------

def test_theme_words_fall_back_to_the_plain_ones():
    import themes
    assert themes.word('submit_answer', 'casefile') == 'Accuse this one'
    assert themes.word('submit_answer', 'plain') == 'Submit Answer'
    assert themes.word('submit_answer', 'some-new-theme') == 'Submit Answer'
    assert {'Accuse this one', 'Submit Answer'} <= themes.all_words()


def test_the_quiz_uses_the_case_file_words_in_every_language(login, ai):
    from translations import translate
    client = login('Ann')
    client.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'en'})
    html = client.get('/quiz').get_data(as_text=True)
    assert 'Accuse this one' in html and 'lying?' in html and 'Submit Answer' not in html
    german = login('Ben', language='de')
    german.post('/start_quiz', data={'category': '1', 'length': '1', 'difficulty': '2', 'language': 'de'})
    assert translate('Accuse this one', 'de') in german.get('/quiz').get_data(as_text=True)


# ---------- Case file layouts --------------------------------------------------------------------------------------

def test_the_quiz_has_the_case_file_layout(login, ai):
    client = login('Ann')
    client.post('/start_quiz', data={'category': '1', 'length': '2', 'difficulty': '2', 'language': 'en'})
    html = client.get('/quiz').get_data(as_text=True)
    assert 'class="quiz-case">Case: ' in html and 'Evidence 1 of 5' in html
    assert html.count('class="is-now"') == 1 and html.count('<span class=""></span>') == 4  # One mark per round
    assert '<span class="letter">1</span>' in html and 'data-pick-label="Suspect"' in html
    for needed in ('id="quizForm"', 'id="submitAnswer"', 'id="quizFinn"', 'id="quizReveal"'):  # What the script uses
        assert needed in html


def test_phones_find_language_and_dark_mode_in_the_menu(app):
    html = app.test_client().get('/').get_data(as_text=True)
    tools = html.split('nav-tools')[1].split('</li>')[0]
    assert 'data-theme-toggle' in tools and tools.count('/language/') == 7
    assert 'class="dropdown d-none d-sm-block"' in html and 'theme-toggle d-none d-sm-grid' in html


def test_setup_has_the_compact_investigation_form(login):
    html = login('Ann').get('/setup').get_data(as_text=True)
    form = html.split('class="wf-card wf-card-pad investigation"')[1].split('</form>')[0]
    for step in ('Pick a lead', 'How long', 'How hard', '>Language<'):
        assert step in form
    assert 'Start the investigation' in form and form.count('class="together-box"') == 2
    assert 'formaction="/live/new"' in form and 'formaction="/class/new"' in form
