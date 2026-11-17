"""Public topic pages for search engines (rules in topic_pages.py): /topic/<slug> with a sample round anyone can
play, the /topics index by category, sitemap.xml and robots.txt. Routes register on the app when app.py imports this.
"""
import json
import random
import time

from flask import Response, abort, redirect, render_template, request, session, url_for

import topic_pages as rules
from app import app, db, hidden_fingerprints, review_round, today_utc
from badges import TOPIC_CATEGORY
from default_text import ALL_TOPICS, TOPICS
from models import DailyChallenge, QuizSession

SLUGS = rules.slug_map(ALL_TOPICS)
SLUG_OF = {topic: slug for slug, topic in SLUGS.items()}
CATEGORY_SLUGS = {rules.slugify(category): category for category in TOPICS}
SAMPLE_GAMES = 30      # Newest finished games of a topic searched for a sample round
RELATED = 8            # Other topics of the same category linked from a page
SITEMAP_SECONDS = 3600  # The sitemap's list of topics with rounds is worked out at most once an hour per server
HELD = 'topic_round'   # Session key: [slug, 'q' or 'd', row id, round index] of the round shown, so the answer is
                       # checked against the real one and the page itself never says which is the fake


def topic_url(topic):
    return url_for('topic_page', slug=SLUG_OF[topic]) if topic in SLUG_OF else None


app.jinja_env.globals['topic_url'] = topic_url


def sample_rounds(topic):
    """[((kind, row id, index), round, explanation, quote)] of rounds already played about the topic: finished games
    made from Wikipedia, and past daily challenges (never today's: that would spoil it)."""
    hidden = hidden_fingerprints()
    found = []
    games = (QuizSession.query.filter(QuizSession.topic == topic, QuizSession.is_completed.is_(True),
                                      QuizSession.source_url.isnot(None), QuizSession.practice.is_(None),
                                      QuizSession.daily_challenge_id.is_(None))
             .order_by(QuizSession.id.desc()).limit(SAMPLE_GAMES).all())
    dailies = DailyChallenge.query.filter(DailyChallenge.topic == topic, DailyChallenge.date < today_utc()).all()
    for kind, rows in (('q', games), ('d', dailies)):
        for row in rows:
            rounds = json.loads(row.questions_json)
            explanations = json.loads(row.explanations_json or '[]')
            quotes = json.loads(row.quotes_json or '[]')
            for index, statements in rules.playable_rounds(rounds, hidden):
                found.append(((kind, row.id, index), statements,
                              explanations[index] if index < len(explanations) else None,
                              quotes[index] if index < len(quotes) else None))
    return found


def held_round(slug):
    """The round this visitor was shown on this page, looked up again, or None."""
    held = session.get(HELD)
    if not held or held[0] != slug:
        return None
    model = QuizSession if held[1] == 'q' else DailyChallenge
    row = db.session.get(model, held[2])
    if not row:
        return None
    rounds = json.loads(row.questions_json)
    index = held[3]
    if not 0 <= index < len(rounds):
        return None
    explanations = json.loads(row.explanations_json or '[]')
    quotes = json.loads(row.quotes_json or '[]')
    return (rounds[index], explanations[index] if index < len(explanations) else None,
            quotes[index] if index < len(quotes) else None, row.source_url)


@app.route('/topic/<slug>', methods=['GET', 'POST'])
def topic_page(slug):
    topic = SLUGS.get(slug)
    if not topic:
        canonical = rules.slugify(slug)  # A visitor's own spelling of an existing page: send them to it
        if canonical in SLUGS and canonical != slug:
            return redirect(url_for('topic_page', slug=canonical), 301)
        abort(404)
    category = TOPIC_CATEGORY.get(topic)
    related = sorted(t for t in TOPICS.get(category, []) if t != topic)
    related = random.Random(slug).sample(related, min(RELATED, len(related)))
    common = dict(topic=topic, slug=slug, category=category, category_slug=rules.slugify(category or ''),
                  related=related, description=rules.description(topic, category))

    if request.method == 'POST':
        held = held_round(slug)
        picked = request.form.get('option')
        if not held or picked not in held[0]:
            return redirect(url_for('topic_page', slug=slug))
        session.pop(HELD, None)
        statements, explanation, quote, source_url = held
        row = {**review_round(statements, explanation, quote), 'picked': picked,
               'correct': statements.get(picked) is False, 'timed_out': False}
        return render_template('topic.html', review=[row], source_url=source_url, has_round=True, **common)

    sample = rules.pick(sample_rounds(topic), f'{slug}:{today_utc()}')
    if sample:
        (kind, row_id, index), statements, _, _ = sample
        session[HELD] = [slug, kind, row_id, index]
        options = list(statements)
        random.Random(f'{slug}:{today_utc()}').shuffle(options)
    else:
        options = None
    return render_template('topic.html', options=options, has_round=bool(sample), **common)


@app.route('/topics')
@app.route('/topics/<category_slug>')
def topics_index(category_slug=None):
    """Every topic page, by category: the category list, or one category's topics."""
    if category_slug is None:
        return render_template('topics.html', categories=[(c, rules.slugify(c), len(TOPICS[c])) for c in sorted(TOPICS)],
                               category=None)
    category = CATEGORY_SLUGS.get(category_slug)
    if not category:
        abort(404)
    return render_template('topics.html', category=category, category_slug=category_slug,
                           topics=sorted(TOPICS[category], key=str.casefold))


_sitemap_cache = {'at': 0.0, 'topics': None}


def topics_with_rounds():
    """The built-in topics that have at least one sample round (cached for SITEMAP_SECONDS)."""
    if _sitemap_cache['topics'] is None or time.monotonic() - _sitemap_cache['at'] > SITEMAP_SECONDS:
        played = {t for (t,) in db.session.query(QuizSession.topic).filter(
            QuizSession.is_completed.is_(True), QuizSession.source_url.isnot(None),
            QuizSession.practice.is_(None), QuizSession.daily_challenge_id.is_(None)).distinct()}
        played |= {t for (t,) in db.session.query(DailyChallenge.topic).filter(DailyChallenge.date < today_utc()).distinct()}
        _sitemap_cache.update(at=time.monotonic(), topics=sorted(played & ALL_TOPICS))
    return _sitemap_cache['topics']


@app.route('/sitemap.xml')
def sitemap():
    pages = [url_for('index', _external=True), url_for('topics_index', _external=True), url_for('try_round', _external=True)]
    pages += [url_for('topics_index', category_slug=rules.slugify(c), _external=True) for c in sorted(TOPICS)]
    pages += [url_for('topic_page', slug=SLUG_OF[t], _external=True) for t in topics_with_rounds()]
    xml = render_template('sitemap.xml', pages=pages)
    return Response(xml, mimetype='application/xml', headers={'Cache-Control': 'public, max-age=3600'})


@app.route('/robots.txt')
def robots():
    """Pages for players' own things (accounts, games in progress, class rooms) aren't for search engines."""
    lines = ['User-agent: *'] + [f'Disallow: {path}' for path in (
        '/admin', '/account', '/setup', '/quiz', '/results', '/review', '/history', '/analysis', '/class', '/live',
        '/practice', '/push', '/cron', '/api', '/report', '/tour', '/fakes/new')]
    lines += ['', f'Sitemap: {url_for("sitemap", _external=True)}', '']
    return Response('\n'.join(lines), mimetype='text/plain', headers={'Cache-Control': 'public, max-age=86400'})
