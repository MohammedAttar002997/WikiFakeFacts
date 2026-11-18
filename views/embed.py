"""The embeddable round of the day (rules in embed.py): /embed/daily is the widget other sites show in an iframe,
/embed the page where a site owner picks a language and theme and copies the code. Framing is allowed only under
/embed/ (security_headers in app.py). Routes register on the app when app.py imports this.
"""
import json

from flask import g, render_template, request, url_for

import embed as rules
from app import app, daily_topic, db, hidden_fingerprints, review_round, today_utc
from default_text import LANGUAGE_OPTIONS
from models import DailyChallenge
from translations import UI_LANGUAGES


def past_dailies(language_name, today):
    """[(id, rounds)] of the latest past daily challenges in a language, newest first, and the rows by id."""
    rows = (DailyChallenge.query.filter(DailyChallenge.date < today, DailyChallenge.language == language_name)
            .order_by(DailyChallenge.date.desc()).limit(rules.LOOKBACK).all())
    return [(row.id, json.loads(row.questions_json)) for row in rows], {row.id: row for row in rows}


def answered(today, hidden):
    """(daily row, round, options, picked) when the request is an answer to a shown round, else None."""
    daily_id, index, pick = (request.args.get(key, type=int) for key in ('d', 'r', 'pick'))
    if None in (daily_id, index, pick):
        return None
    row = db.session.get(DailyChallenge, daily_id)
    if not row or row.date >= today:
        return None
    statements = rules.playable_round(json.loads(row.questions_json), index, hidden)
    if not statements:
        return None
    options = rules.option_order(statements, f'{row.id}:{index}')
    if not 0 <= pick < len(options):
        return None
    return row, index, statements, options[pick]


@app.route('/embed/daily')
def embed_daily():
    lang, theme = rules.settings(request.args, UI_LANGUAGES)
    g.ui_language = lang  # The widget's language comes from its address, never from (or into) the session
    today, hidden = today_utc(), hidden_fingerprints()
    common = dict(theme=theme, lang=lang, today_topic=daily_topic(today))

    answer = answered(today, hidden)
    if answer:
        row, index, statements, picked = answer
        explanations = json.loads(row.explanations_json or '[]')
        quotes = json.loads(row.quotes_json or '[]')
        review = {**review_round(statements, explanations[index] if index < len(explanations) else None,
                                 quotes[index] if index < len(quotes) else None),
                  'picked': picked, 'correct': statements.get(picked) is False, 'timed_out': False}
        return render_template('embed_daily.html', review=[review], daily=row, source_url=row.source_url, **common)

    dailies, rows = past_dailies(LANGUAGE_OPTIONS.get(lang, 'English'), today)
    if not dailies and lang != 'en':
        dailies, rows = past_dailies('English', today)  # No daily in this language yet: English facts, local UI
    chosen = rules.round_of_the_day(dailies, hidden, today)
    if not chosen:
        return render_template('embed_daily.html', daily=None, **common)
    daily_id, index, statements = chosen
    return render_template('embed_daily.html', daily=rows[daily_id], round_index=index,
                           options=rules.option_order(statements, f'{daily_id}:{index}'), **common)


@app.route('/embed')
def embed_page():
    """For site owners: the widget's code, with a preview."""
    lang, theme = rules.settings(request.args, UI_LANGUAGES)
    src = url_for('embed_daily', lang=lang, theme=theme, _external=True)
    return render_template('embed.html', lang=lang, theme=theme, themes=rules.THEMES, snippet=rules.snippet(src, 'WikiFakeFact'),
                           height=rules.HEIGHT, max_width=rules.MAX_WIDTH,
                           widget_base=url_for('embed_daily', _external=True))
