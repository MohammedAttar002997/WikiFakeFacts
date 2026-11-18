"""The embeddable round of the day: the rules (embed.py) and the widget and code page (views/embed.py)."""
import json
from datetime import timedelta

import embed as rules
from app import db
from hidden_rounds import fingerprint
from models import DailyChallenge, HiddenRound, utcnow

ROUND = {'It is in Paris.': True, 'It opened in 1889.': True, 'It is painted green.': False,
         'It is made of iron.': True}
BROKEN = {'Only': True, 'two': False}


# ---------- Rules (embed.py) --------------------------------------------------------------------------------------

def test_settings_fall_back_to_english_and_auto():
    assert rules.settings({'lang': 'de', 'theme': 'dark'}, ['en', 'de']) == ('de', 'dark')
    assert rules.settings({'lang': 'xx', 'theme': 'neon'}, ['en', 'de']) == ('en', 'auto')
    assert rules.settings({}, ['en']) == ('en', 'auto')


def test_round_of_the_day_takes_the_newest_daily_with_a_playable_round():
    assert rules.round_of_the_day([(9, [BROKEN]), (8, [ROUND])], set(), '2026-11-18') == (8, 0, ROUND)
    assert rules.round_of_the_day([(8, [ROUND])], {fingerprint(ROUND)}, '2026-11-18') is None
    assert rules.round_of_the_day([], set(), '2026-11-18') is None


def test_the_round_is_the_same_all_day_and_changes_over_days():
    rounds = [{**{k: v for k, v in ROUND.items() if k != 'It is made of iron.'}, f'Fact {i}': True} for i in range(5)]
    assert rules.round_of_the_day([(1, rounds)], set(), 'a') == rules.round_of_the_day([(1, rounds)], set(), 'a')
    assert len({rules.round_of_the_day([(1, rounds)], set(), f'd{n}')[1] for n in range(40)}) > 1


def test_option_order_is_steady_and_keeps_every_statement():
    assert rules.option_order(ROUND, '1:0') == rules.option_order(ROUND, '1:0')
    assert sorted(rules.option_order(ROUND, '1:0')) == sorted(ROUND)


def test_playable_round_only_returns_good_unhidden_rounds():
    assert rules.playable_round([BROKEN, ROUND], 1, set()) == ROUND
    assert rules.playable_round([BROKEN, ROUND], 0, set()) is None
    assert rules.playable_round([ROUND], 0, {fingerprint(ROUND)}) is None


def test_snippet_is_an_escaped_responsive_iframe():
    code = rules.snippet('https://x.test/embed/daily?lang=de&theme=dark', 'WikiFakeFact')
    assert code.startswith('<iframe src="https://x.test/embed/daily?lang=de&amp;theme=dark"')
    assert 'width="100%"' in code and f'height="{rules.HEIGHT}"' in code and 'max-width:480px' in code


# ---------- The widget --------------------------------------------------------------------------------------------

def daily(days_ago=1, language='English', rounds=(ROUND,), topic='Eiffel Tower'):
    row = DailyChallenge(date=(utcnow() - timedelta(days=days_ago)).strftime('%Y-%m-%d'), language=language,
                         topic=topic, questions_json=json.dumps(list(rounds)),
                         explanations_json=json.dumps(['It was never green.'] * len(rounds)),
                         quotes_json=json.dumps(['The tower is painted brown.'] * len(rounds)),
                         source_url='https://en.wikipedia.org/wiki/Eiffel_Tower')
    db.session.add(row)
    db.session.commit()
    return row


def answer_link(row, statement, index=0):
    """The address the widget's form leads to when `statement` is picked."""
    return f'/embed/daily?d={row.id}&r={index}&pick={rules.option_order(ROUND, f"{row.id}:{index}").index(statement)}'


def test_the_widget_shows_yesterdays_round_and_may_be_framed(app, app_ctx):
    daily()
    response = app.test_client().get('/embed/daily')
    html = response.get_data(as_text=True)
    assert 'It is painted green.' in html and 'name="pick"' in html and 'Eiffel Tower' in html
    assert response.headers['Content-Security-Policy'] == 'frame-ancestors *'
    assert 'X-Frame-Options' not in response.headers
    assert '<base target="_blank">' in html and 'target="_self"' in html and 'noindex' in html
    assert 'Set-Cookie' not in response.headers  # Nothing to keep: works where browsers block cookies


def test_other_pages_still_refuse_framing(app):
    response = app.test_client().get('/embed')
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Content-Security-Policy'] == "frame-ancestors 'none'"


def test_todays_daily_is_never_shown(app, app_ctx):
    daily(days_ago=0)
    assert 'No round yet' in app.test_client().get('/embed/daily').get_data(as_text=True)


def test_answering_shows_the_review(app, app_ctx):
    row = daily()
    client = app.test_client()
    form = client.get('/embed/daily').get_data(as_text=True).split('id="embedForm"')[1]
    assert f'name="d" value="{row.id}"' in form and 'name="r" value="0"' in form  # What the links below rely on
    html = client.get(answer_link(row, 'It is painted green.')).get_data(as_text=True)
    assert 'Answer review' in html and 'Spotted it' in html and 'It was never green.' in html
    assert 'Missed' in client.get(answer_link(row, 'It is in Paris.')).get_data(as_text=True)


def test_answers_to_todays_or_hidden_or_made_up_rounds_show_the_question(app, app_ctx):
    today, past = daily(days_ago=0), daily()
    client = app.test_client()
    for query in (f'd={today.id}&r=0&pick=0', f'd={past.id}&r=5&pick=0', f'd={past.id}&r=0&pick=9', 'd=999&r=0&pick=0'):
        assert 'Answer review' not in client.get(f'/embed/daily?{query}').get_data(as_text=True)
    db.session.add(HiddenRound(fingerprint=fingerprint(ROUND), topic='Eiffel Tower', statements_json=json.dumps(ROUND)))
    db.session.commit()
    assert 'Answer review' not in client.get(f'/embed/daily?d={past.id}&r=0&pick=0').get_data(as_text=True)


def test_language_comes_from_the_address_and_never_from_or_into_the_session(app, app_ctx):
    daily()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['language'] = 'fr'
    html = client.get('/embed/daily?lang=de').get_data(as_text=True)
    assert 'lang="de"' in html and 'Heutige Challenge' in html
    assert 'lang="en"' in client.get('/embed/daily').get_data(as_text=True)
    with client.session_transaction() as sess:
        assert sess['language'] == 'fr'


def test_a_language_with_its_own_daily_gets_it(app, app_ctx):
    daily(topic='Eiffel Tower')
    daily(language='Deutsch (German)', topic='Kölner Dom',
          rounds=[{'Er steht in Köln.': True, 'Er ist gotisch.': True, 'Er ist grün.': False, 'Er hat zwei Türme.': True}])
    assert 'Er ist grün.' in app.test_client().get('/embed/daily?lang=de').get_data(as_text=True)
    assert 'It is painted green.' in app.test_client().get('/embed/daily?lang=it').get_data(as_text=True)


def test_dark_theme_and_the_link_to_todays_challenge(app, app_ctx):
    daily()
    html = app.test_client().get('/embed/daily?theme=dark').get_data(as_text=True)
    assert 'data-bs-theme="dark"' in html and "Today's challenge" in html and '/daily' in html


# ---------- The code page -----------------------------------------------------------------------------------------

def test_the_code_page_has_the_snippet_and_a_preview(app):
    html = app.test_client().get('/embed?lang=es&theme=light').get_data(as_text=True)
    code = html.split('id="embedCode"')[1].split('</textarea>')[0]
    assert '&lt;iframe src=' in code and '/embed/daily?lang=es&amp;amp;theme=light' in code
    assert 'id="embedPreview"' in html and 'Copy code' in html


def test_the_footer_links_the_embed_page(app):
    assert 'href="/embed"' in app.test_client().get('/').get_data(as_text=True)
