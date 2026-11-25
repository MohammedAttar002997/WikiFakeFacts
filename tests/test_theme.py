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
