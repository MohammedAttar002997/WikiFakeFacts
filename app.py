import hmac
import io
import os
import json
import random
import secrets
import traceback
from datetime import timedelta

import click
from flask import Flask, abort, g, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import case, func, inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import aliased
from sqlalchemy.orm.attributes import set_committed_value
from flask_migrate import Migrate, stamp, upgrade
from flask_wtf.csrf import CSRFError, CSRFProtect
from urllib.parse import urlparse
from alembic.migration import MigrationContext
from alembic.operations import Operations
from models import (db, utcnow, User, Score, QuizSession, CustomContent, ChatbotSession, PlayerAnalysis, DailyChallenge,
                    UserBadge, PinAttempt, RateLimit, ErrorLog, LiveMatch, RoundReport, ReadyQuiz, HiddenRound,
                    PushSubscription)
from data import get_ai_response, get_ai_analysis, AIAnalysis
# Used by views/fakes.py as core.*, so tests stub them here
from data import article_text, check_player_fake  # noqa: F401
from data import check_topic_audience, resolve_topic, suggest_topics
from default_text import CATEGORIES, MENU, TOPICS, ALL_TOPICS, SURPRISE, GAME_LENGTH_OPTIONS, DIFFICULTY_OPTIONS, LANGUAGE_OPTIONS, LEADERBOARD_PERIODS
from translations import (UI_LANGUAGES, current_language, format_date, template_context, template_globals, translate as _,
                          translate_plural as _n)
from content_extractor import (MAX_CONTENT_CHARS, ContentError, extract_text_from_upload, extract_text_from_url,
                               validate_content_length)
from chatbot import get_chatbot_response  # noqa: F401  (views/help_chat.py calls it through here, so tests can stub it)
from badges import BADGES, BADGES_BY_KEY, award_badges, compute_streaks, player_streaks
from rating import rank as rank_players
from og_card import result_card
import ready_pool
import hidden_rounds
import practice as practice_rules
import fake_diff
import outfits
import any_topic
import leagues


# Vercel serves public/ from its CDN without starting the app; locally Flask serves the same files at /static
app = Flask(__name__, static_folder='public/static')
DEV_SECRET_KEY = 'hackathon-secret-key'  # Only for local development; serve.py refuses to run with it
app.secret_key = os.environ.get('SECRET_KEY') or DEV_SECRET_KEY
ON_VERCEL = bool(os.environ.get('VERCEL'))  # Vercel sets VERCEL=1 for builds and functions
if ON_VERCEL and app.secret_key == DEV_SECRET_KEY:
    raise RuntimeError('SECRET_KEY is not set. Add a long random value in the Vercel project settings.')
# Browsers don't send the session cookie on cross-site POSTs (a second layer next to CSRF tokens)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Set SESSION_COOKIE_SECURE=1 when the site is served over HTTPS, so the cookie is never sent unencrypted
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE') == '1'
# Donation page (Ko-fi, Buy Me a Coffee, ...) linked in the footer; no link unless it's set to an https URL
app.config['DONATION_URL'] = os.environ.get('DONATION_URL', '').strip()
# Players (by name, comma-separated) who may open /admin. Without any, the page doesn't exist.
app.config['ADMIN_USERS'] = os.environ.get('ADMIN_USERS', '')
# Shared with the timers that call /cron/refill (Vercel sends it as a Bearer token by itself; see ready_pool.py)
app.config['CRON_SECRET'] = os.environ.get('CRON_SECRET', '')
# Daily-challenge reminders (reminders.py); without the key pair (make_vapid_keys.py) the option isn't offered
app.config['VAPID_PUBLIC_KEY'] = os.environ.get('VAPID_PUBLIC_KEY', '')
app.config['VAPID_PRIVATE_KEY'] = os.environ.get('VAPID_PRIVATE_KEY', '')
# Who sends the pushes, for push services to contact if something goes wrong (a mailto: or https address)
app.config['VAPID_SUBJECT'] = os.environ.get('VAPID_SUBJECT', 'https://www.wikifakefacts.com')

# File upload configuration (uploads are read in memory, never saved)
ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}
# Vercel refuses request bodies over 4.5 MB with its own error page, before the app sees them; 4 MB leaves room for
# the rest of the form. The custom quiz page also checks the size in the browser (data-max-bytes) before uploading.
MAX_FILE_MB = 4
MAX_FILE_SIZE = MAX_FILE_MB * 1024 * 1024

# Profile photos: re-encoded as square WebP images of this size and stored in the database
AVATAR_SIZE = 256
AVATAR_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}
AVATAR_MAX_PIXELS = 40_000_000  # Bigger pictures are refused before they're decoded
# Big pictures are shrunk in the browser to this many pixels on their longest side before uploading (account.html),
# so phone photos fit under the upload limit; the server then crops them to AVATAR_SIZE anyway
PHOTO_MAX_SIDE = 1024

app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Database configuration
basedir = os.path.abspath(os.path.dirname(__file__))


def database_url():
    """DATABASE_URL (SQLite by default), with Postgres URLs as Neon and Vercel give them pointed at psycopg 3."""
    url = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(basedir, 'quiz.sqlite')
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


app.config['SQLALCHEMY_DATABASE_URI'] = database_url()
# Serverless instances sit idle between requests, so check pooled connections before using them
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Every POST must carry the session's CSRF token (forms: hidden csrf_token field; fetch: X-CSRFToken header).
# Tokens last as long as the session, so a quiz left open for a while can still be submitted.
app.config['WTF_CSRF_TIME_LIMIT'] = None
csrf = CSRFProtect(app)
# render_as_batch lets Alembic alter SQLite tables (it recreates them instead of using unsupported ALTERs)
migrate = Migrate(app, db, directory=os.path.join(basedir, 'migrations'), render_as_batch=True)

# Wrong-PIN throttling per lowercase name (stored in pin_attempt): 5 wrong PINs lock the name for 5 minutes
MAX_PIN_ATTEMPTS = 5
# … and at most this many PIN checks per name and day (stored in rate_limit). Without it, 5 guesses every 5 minutes
# find any 4-digit PIN within about two days; with it, guessing one takes about half a year.
PIN_DAILY_LIMIT = 25
PIN_DAY = timedelta(days=1)
# Requests per key and window, counted in the database so every server instance shares them. They cap what one
# visitor can cost: AI calls (chat, quizzes) and rows (accounts, chats).
RATE_LIMITS = {
    'chat': (20, timedelta(minutes=10)),     # help-chat messages per address
    'quiz': (20, timedelta(hours=1)),        # AI-generated quizzes per player
    'quiz_ip': (40, timedelta(hours=1)),     # … and per address, across accounts
    'signup': (10, timedelta(hours=1)),      # new accounts per address
    'report': (20, timedelta(hours=1)),      # round reports per player
    'push': (20, timedelta(hours=1)),        # reminder switches on/off per player
    'fake': (10, timedelta(hours=1)),        # player fakes sent to the AI check, per player
    'suggest': (150, timedelta(minutes=10)),  # topic suggestions while typing, per player
    'topic': (30, timedelta(hours=1)),        # typed topics sent to the AI age check, per player
    'follow': (60, timedelta(hours=1)),       # players followed, per player
}
MAX_CHAT_MESSAGE = 500  # Characters
MAX_NAME_LENGTH = 30  # For new names; players who signed up before the limit keep theirs
PIN_LOCKOUT = timedelta(minutes=5)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def make_avatar(file):
    """An uploaded picture cropped to a square AVATAR_SIZE WebP, or None if it isn't a usable image.
    Re-encoding keeps only the pixels, so metadata (like a photo's GPS location) and anything else hidden in the
    file is dropped."""
    try:
        with Image.open(file) as image:
            if image.format not in AVATAR_FORMATS or image.width * image.height > AVATAR_MAX_PIXELS:
                return None
            image = ImageOps.exif_transpose(image)  # Phone photos are often stored sideways with a rotation tag
            image = ImageOps.fit(image.convert('RGBA'), (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, 'WEBP', quality=85)
            return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return None


@app.context_processor
def inject_translations():
    return template_context()


@app.before_request
def forget_cached_player():
    # current_user() caches the player on g, which can outlive a request (e.g. inside an app context in tests)
    g.pop('me', None)


@app.context_processor
def inject_player():
    me = current_user()
    donation_url = app.config.get('DONATION_URL') or ''
    # What this player's Finn wears, for every page's _finn.html (outfits.py)
    outfit = outfits.parse(me.finn_outfit) if me and me.finn_outfit else {}
    return {'me': me, 'challenge_news': count_challenge_news(me) if me else 0, 'is_admin': admin_views.is_admin(me),
            'donation_url': donation_url if donation_url.startswith('https://') else None,
            'finn_style': outfits.style(outfit), 'finn_extra': outfits.extra(outfit)}


app.jinja_env.globals.update(template_globals())
app.add_template_filter(format_date, 'date')
app.add_template_filter(lambda value: format_date(value, with_time=True), 'datetime')


@app.after_request
def security_headers(response):
    # No framing by other sites (clickjacking), no guessing content types, and only the origin in cross-site
    # referrers so share codes and quiz pages don't leak to links people follow
    if request.path.startswith('/embed/'):
        response.headers['Content-Security-Policy'] = 'frame-ancestors *'  # The widget (views/embed.py) is for framing
    else:
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Content-Security-Policy', "frame-ancestors 'none'")
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response


@app.errorhandler(404)
def page_not_found(error):
    return render_template('errors.html', code=404, title=_("Page not found"),
                           message=_("This page doesn't exist or is no longer available.")), 404


@app.errorhandler(500)
def server_error(error):
    # Flask has already logged it; keep a copy that outlasts Vercel's logs
    save_error(getattr(error, 'original_exception', None) or error)
    return render_template('errors.html', code=500, title=_("Something went wrong"),
                           message=_("Sorry, something went wrong on our side. Please try again in a moment.")), 500


@app.errorhandler(413)
def file_too_large(error):
    """An upload over MAX_CONTENT_LENGTH: back to the form it came from, with a message."""
    flash(_("That file is too big. The limit is {n} MB.").format(n=MAX_FILE_MB), "danger")
    referrer = urlparse(request.referrer or '')
    same_site = referrer.netloc == request.host
    return redirect((local_path(referrer.path) if same_site else None) or url_for('index'))


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    """A form or request without a valid CSRF token: usually an expired session or a page from another site."""
    message = _("Your session expired or the request couldn't be verified. Please try again.")
    if request.path.startswith('/api/'):
        return jsonify({'error': message}), 400
    flash(message, "warning")
    # Send the player back to the page they came from, but only if it's on this site
    referrer = urlparse(request.referrer or '')
    same_site = referrer.netloc == request.host and referrer.path.startswith('/')
    return redirect(referrer.path if same_site else url_for('index'))


@app.route('/language/<code>')
def set_language(code):
    """Switch the interface (and default quiz) language, then go back to the page the player was on."""
    if code in UI_LANGUAGES:
        session['language'] = code
    # Only follow local paths, never another site
    return redirect(local_path(request.args.get('next')) or '/')


# Schema of the first Alembic migration (migrations/versions/0001_baseline.py)
BASELINE_REVISION = '0001_baseline'
BASELINE_TABLES = ['user', 'score', 'quiz_session', 'custom_content', 'chatbot_session', 'player_analysis',
                   'daily_challenge']
# Columns added before migrations existed, by hand with ALTER TABLE
PRE_MIGRATION_COLUMNS = [
    ('user', 'pin_hash', 'VARCHAR(255)'),
    ('quiz_session', 'explanations_json', 'TEXT'),
    ('quiz_session', 'answers_json', 'TEXT'),
    ('score', 'quiz_session_id', 'INTEGER'),
    ('quiz_session', 'daily_challenge_id', 'INTEGER'),
]
PRE_MIGRATION_FOREIGN_KEYS = [
    ('score', 'quiz_session_id', 'quiz_session'),
    ('quiz_session', 'daily_challenge_id', 'daily_challenge'),
]


def init_database():
    """Bring the database up to date with the Alembic migrations in migrations/."""
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if 'user' in tables and 'alembic_version' not in tables:
        # Database from before migrations: bring it up to the baseline schema, then record that it's there
        with db.engine.begin() as conn:
            for table, column, column_type in PRE_MIGRATION_COLUMNS:
                if table in tables and column not in [c['name'] for c in inspector.get_columns(table)]:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {column_type}'))
        for table in BASELINE_TABLES:
            if table not in tables:
                db.metadata.tables[table].create(db.engine)
        # ALTER TABLE ADD COLUMN didn't add foreign keys; SQLite can only add them by rebuilding the table
        with db.engine.begin() as conn:
            ops = Operations(MigrationContext.configure(conn))
            for table, column, target in PRE_MIGRATION_FOREIGN_KEYS:
                if column not in [c for fk in inspect(conn).get_foreign_keys(table) for c in fk['constrained_columns']]:
                    with ops.batch_alter_table(table, recreate='always') as batch:
                        batch.create_foreign_key(f'fk_{table}_{column}', target, [column], ['id'])
        stamp(revision=BASELINE_REVISION)
    upgrade()


def create_quiz_session(user, topic, difficulty, questions_count, language_name, custom_content=None,
                        custom_title=None, time_limit=None, rematch_for_id=None, event_week=None):
    """Generate a quiz, save it, and make it the player's current quiz."""
    quiz = get_ai_response(questions_count, topic, difficulty, language_name, custom_content=custom_content)
    if custom_content:
        # Label custom quizzes by their subject so progress/leaderboard show a real topic:
        # the user's own title wins, then the AI-detected topic
        topic = (custom_title or '').strip() or quiz.topic or "Custom Content"

    return save_quiz_session(user, topic, difficulty, quiz.rounds, quiz.explanations, quiz.source,
                             time_limit=time_limit, rematch_for_id=rematch_for_id, quotes=quiz.quotes,
                             source_url=quiz.source_url, event_week=event_week)


def save_quiz_session(user, topic, difficulty, quiz_data, explanations, source, daily_challenge_id=None,
                      time_limit=None, challenge_of_id=None, rematch_for_id=None, quotes=None, source_url=None,
                      event_week=None, is_practice=False):
    """Save already-generated questions as the player's current quiz."""
    quiz_session = QuizSession(
        user_id=user.id,
        topic=topic[:100],
        difficulty=difficulty,
        questions_json=json.dumps(quiz_data),
        explanations_json=json.dumps(explanations),
        quotes_json=json.dumps(quotes) if quotes is not None else None,
        source_url=source_url,
        answers_json='[]',
        total_questions=len(quiz_data),
        current_question_index=0,
        score=0,
        daily_challenge_id=daily_challenge_id,
        time_limit=time_limit,
        challenge_of_id=challenge_of_id,
        rematch_for_id=rematch_for_id,
        event_week=event_week,
        practice=True if is_practice else None,
        # A rematch is played through its share link, so it needs one even if its results page is never shown
        share_code=secrets.token_urlsafe(8) if rematch_for_id else None
    )
    db.session.add(quiz_session)
    db.session.commit()

    session['quiz_session_id'] = quiz_session.id
    session['quiz_source'] = source
    return quiz_session



TIMED_ROUND_SECONDS = 20
TIMER_GRACE_SECONDS = 3  # Allowance for page load and network delay before a late answer counts as timed out

DAILY_DIFFICULTY = DIFFICULTY_OPTIONS["2"]  # Medium
DAILY_ROUNDS = 5


def today_utc():
    return utcnow().strftime('%Y-%m-%d')


def daily_topic(date):
    """Same topic for everyone on a given date, picked from the built-in categories."""
    topics = sorted(topic for topics in MENU.values() for topic in topics)
    return random.Random(date).choice(topics)


def get_daily_challenge(date, language_name):
    """Today's shared quiz in this language, generating it for the first player who starts it."""
    challenge = DailyChallenge.query.filter_by(date=date, language=language_name).first()
    if challenge:
        return challenge

    topic = daily_topic(date)
    quiz = get_ai_response(DAILY_ROUNDS, topic, DAILY_DIFFICULTY, language_name)
    challenge = DailyChallenge(date=date, language=language_name, topic=topic, source=quiz.source,
                               questions_json=json.dumps(quiz.rounds), explanations_json=json.dumps(quiz.explanations),
                               quotes_json=json.dumps(quiz.quotes), source_url=quiz.source_url)
    db.session.add(challenge)
    try:
        db.session.commit()
    except IntegrityError:
        # Another player generated it at the same moment; use theirs so everyone shares one quiz
        db.session.rollback()
        challenge = DailyChallenge.query.filter_by(date=date, language=language_name).first()
    return challenge


def daily_attempt(user, date):
    """The player's quiz for the given day's challenge (any language), or None."""
    return (QuizSession.query.join(DailyChallenge, QuizSession.daily_challenge_id == DailyChallenge.id)
            .filter(QuizSession.user_id == user.id, DailyChallenge.date == date).first())


EVENT_DIFFICULTY = DIFFICULTY_OPTIONS["2"]  # Medium
EVENT_ROUNDS = 5
EVENT_BEST_OF = 3  # Quizzes per player that count each week, so playing more helps without winning by itself


def week_start(day=None):
    """The Monday of the UTC week `day` (default today) is in; weekly events run Monday to Sunday."""
    day = day or utcnow().date()
    return day - timedelta(days=day.weekday())


def event_week(day=None):
    """ISO week key, like '2026-W45'."""
    year, week, _weekday = week_start(day).isocalendar()
    return f'{year}-W{week:02d}'


def event_theme(day=None):
    """The category featured in `day`'s week: each category in turn, a different one every week."""
    names = sorted(MENU)
    return names[week_start(day).toordinal() // 7 % len(names)]


def event_standings(week):
    """[{user, points, quizzes}] for a week's event, best first. Points are the sum of each player's EVENT_BEST_OF
    best scores; of two players with the same points, the one who needed fewer quizzes ranks higher."""
    rows = (db.session.query(Score, User).join(User, Score.user_id == User.id)
            .join(QuizSession, Score.quiz_session_id == QuizSession.id)
            .filter(QuizSession.event_week == week).all())
    by_player = {}
    for score, user in rows:
        by_player.setdefault(user.id, (user, []))[1].append(score.score)
    standings = [{'user': user, 'points': sum(sorted(scores, reverse=True)[:EVENT_BEST_OF]), 'quizzes': len(scores)}
                 for user, scores in by_player.values()]
    standings.sort(key=lambda row: (-row['points'], row['quizzes'], row['user'].username.lower()))
    return standings


def event_topic(user, week, theme):
    """A topic of the theme the player hasn't had in this week's event yet, while there are any left."""
    played = {topic for (topic,) in db.session.query(QuizSession.topic)
              .filter_by(user_id=user.id, event_week=week)}
    return random.choice([t for t in MENU[theme] if t not in played] or MENU[theme])


def flash_new_badges(user):
    for badge in award_badges(user):
        flash(badge['icon'] + ' ' + _("Badge unlocked: {name}!").format(name=_(badge['name'])) + ' '
              + _(badge['description']), "success")


def new_name_problem(name, user=None):
    """Why `name` can't be taken as a new player name (by `user`, when renaming), or None if it can."""
    if len(name) <= 2:
        return _("Please enter a valid name (at least 3 characters).")
    if len(name) > MAX_NAME_LENGTH:
        return _n("Please choose a name of at most {n} character.", "Please choose a name of at most {n} characters.",
                  MAX_NAME_LENGTH)
    taken = User.query.filter(func.lower(User.username) == name.lower())
    if user:
        taken = taken.filter(User.id != user.id)
    if taken.first():
        return _("The name “{name}” is already taken. Please choose a different one.").format(name=name)
    return None


def pin_token(user):
    """Fingerprint of the account's PIN hash, kept in the session: changing the PIN ends the other sessions."""
    return hmac.new(app.secret_key.encode(), (user.pin_hash or '').encode(), 'sha256').hexdigest()[:16]


def sign_in(user):
    session['user_id'] = user.id
    session['pin_token'] = pin_token(user)
    session.pop('username', None)  # Sessions from before user_id named the player instead
    g.me = user


def sign_out():
    for key in ('user_id', 'pin_token', 'username', 'quiz_session_id', 'chat_id', 'next_url'):
        session.pop(key, None)
    g.me = None


def current_user():
    """The signed-in player, or None. The session names the account by id along with a fingerprint of its PIN, so
    it ends when the account is deleted or its PIN changes, and someone who later takes a freed name can't inherit
    it."""
    if 'me' not in g:
        user = db.session.get(User, session['user_id']) if 'user_id' in session else None
        if user and not hmac.compare_digest(session.get('pin_token', ''), pin_token(user)):
            user = None
        if not user and ('user_id' in session or 'username' in session):
            sign_out()
        g.me = user
    return g.me


def local_path(target):
    """`target` if it's a path on this site, else None. Browsers drop tabs and newlines from URLs and read
    backslashes as slashes, so '/<tab>/evil.example' or '/\\evil.example' would lead to another site."""
    if (not target or not target.startswith('/') or target.startswith('//')
            or any(c == '\\' or ord(c) < 0x21 or ord(c) == 0x7f for c in target)):
        return None
    return target


def share_details(quiz_session):
    """Link and message for sharing a finished quiz; the link is created the first time it's needed."""
    if not quiz_session.is_completed:
        return None
    if not quiz_session.share_code:
        quiz_session.share_code = secrets.token_urlsafe(8)
        db.session.commit()
    text = _("I spotted {score} of {total} fake facts about {topic} on WikiFakeFact. Can you beat me?").format(
        score=quiz_session.score, total=quiz_session.total_questions, topic=quiz_session.topic)
    return {'url': url_for('shared_result', code=quiz_session.share_code, _external=True), 'text': text}


def challenge_comparison(quiz_session):
    """How a finished challenge attempt compares with the quiz it replayed, or None."""
    original = db.session.get(QuizSession, quiz_session.challenge_of_id) if quiz_session.challenge_of_id else None
    if not original:
        return None
    opponent = db.session.get(User, original.user_id)
    diff = quiz_session.score - original.score
    return {'opponent': opponent.username, 'their_score': original.score, 'my_score': quiz_session.score,
            'total': quiz_session.total_questions, 'outcome': 'win' if diff > 0 else ('tie' if diff == 0 else 'loss'),
            'code': original.share_code}


def seconds_left(quiz_session):
    """Seconds remaining in the current timed round (negative once it's over)."""
    started = quiz_session.round_started_at or utcnow()
    return quiz_session.time_limit - (utcnow() - started).total_seconds()


def review_round(facts, explanation, quote):
    """What every answer review shows about a round, whoever played it: the statements, why the fake is fake, the
    source sentence, and (fake_diff.py) which words of it the fake changed."""
    fake = next((s for s, real in facts.items() if real is False), None)
    return {'facts': list(facts.items()), 'explanation': explanation, 'quote': quote,
            'diff': fake_diff.compare(fake, quote) if quote else None}


def build_answer_review(quiz_session, opponent=None):
    """Round-by-round answer review: which statement was fake, what the player picked, and why. With `opponent`
    (the other side of a challenge, same questions) each round also says what they picked."""
    questions = json.loads(quiz_session.questions_json)
    answers = json.loads(quiz_session.answers_json or '[]')
    explanations = json.loads(quiz_session.explanations_json or '[]')
    quotes = json.loads(quiz_session.quotes_json or '[]')
    their_answers = json.loads(opponent.answers_json or '[]') if opponent else []

    def outcome(picks, i, facts):
        answered = i < len(picks)
        return {'picked': picks[i] if answered else None,
                'correct': answered and facts.get(picks[i]) is False,
                'timed_out': answered and picks[i] is None}

    review = []
    for i, facts in enumerate(questions[:quiz_session.current_question_index]):
        row = {**review_round(facts, explanations[i] if i < len(explanations) else None,
                              quotes[i] if i < len(quotes) else None), **outcome(answers, i, facts)}
        if opponent:
            row['them'] = outcome(their_answers, i, facts)
        review.append(row)
    return review


def challenge_opponent(quiz_session, vs_id=None):
    """The other finished quiz in a head-to-head with `quiz_session`, or None: the quiz it replayed, or (for the
    original quiz) the challenger's attempt with id `vs_id`."""
    if vs_id is not None:
        attempt = db.session.get(QuizSession, vs_id)
        return attempt if attempt and attempt.challenge_of_id == quiz_session.id and attempt.is_completed else None
    if quiz_session.challenge_of_id:
        original = db.session.get(QuizSession, quiz_session.challenge_of_id)
        return original if original and original.is_completed else None
    return None




def versus_details(quiz_session, opponent):
    """What the answer review shows about the other player in a challenge, and the attempt a rematch starts from."""
    if not opponent:
        return None
    attempt = quiz_session if quiz_session.challenge_of_id else opponent
    return {'name': db.session.get(User, opponent.user_id).username, 'attempt_id': attempt.id,
            'can_rematch': can_rematch(attempt)}


def can_rematch(attempt):
    """Rematches need a fresh quiz on the same topic, which only built-in topics can get (a custom quiz's content
    belongs to the player who uploaded it)."""
    return attempt.topic in ALL_TOPICS


def challengers_query(user):
    """(attempt, its Score, challenger, original quiz) for every finished challenge of one of `user`'s quizzes."""
    original = aliased(QuizSession)
    return (db.session.query(QuizSession, Score, User, original)
            .join(Score, Score.quiz_session_id == QuizSession.id)
            .join(User, QuizSession.user_id == User.id)
            .join(original, QuizSession.challenge_of_id == original.id)
            .filter(original.user_id == user.id, QuizSession.is_completed.is_(True)))


def pending_rematches_query(user):
    """(rematch quiz, its Score, sender) for every finished rematch sent to `user` that they haven't finished."""
    finished_by_user = (select(QuizSession.challenge_of_id)
                        .where(QuizSession.user_id == user.id, QuizSession.is_completed.is_(True),
                               QuizSession.challenge_of_id.isnot(None)))
    return (db.session.query(QuizSession, Score, User)
            .join(Score, Score.quiz_session_id == QuizSession.id)
            .join(User, QuizSession.user_id == User.id)
            .filter(QuizSession.rematch_for_id == user.id, QuizSession.is_completed.is_(True),
                    QuizSession.id.notin_(finished_by_user)))


def count_challenge_news(user):
    """How many challengers and rematches reached `user` since they last opened /challenges."""
    queries = [challengers_query(user), pending_rematches_query(user)]
    if user.challenges_seen_at:
        queries = [q.filter(Score.timestamp > user.challenges_seen_at) for q in queries]
    return sum(q.count() for q in queries)


MIN_COACHING_GAMES = 3  # Fewer games give the AI too little to say anything real, so it used to guess
COACHING_HISTORY = 30  # Newest games the AI coach sees
MISSED_PER_GAME = 5  # Missed rounds per game it sees


def missed_rounds(quiz_session):
    """[{fake, picked}] for each round of a finished quiz the player got wrong (picked None: time ran out)."""
    questions = json.loads(quiz_session.questions_json or '[]')
    answers = json.loads(quiz_session.answers_json or '[]')
    missed = []
    for facts, pick in zip(questions, answers):
        if facts.get(pick) is not False:
            missed.append({'fake': next((s for s, real in facts.items() if real is False), None), 'picked': pick})
    return missed[:MISSED_PER_GAME]


def coaching_history(user_scores):
    """What the AI coach is given: each of the newest games with its score and the facts the player missed, so
    its advice is about what they actually got wrong rather than guesses about the topic."""
    sessions = {q.id: q for q in QuizSession.query.filter(
        QuizSession.id.in_([s.quiz_session_id for s in user_scores if s.quiz_session_id]))}
    return [{
        'topic': s.topic,
        'score': s.score,
        'total': s.total_questions,
        'percentage': s.percentage,
        'difficulty': s.difficulty,
        'date': s.timestamp.strftime("%Y-%m-%d"),
        'missed': missed_rounds(sessions[s.quiz_session_id]) if s.quiz_session_id in sessions else [],
    } for s in user_scores[-COACHING_HISTORY:]]


def get_topic_stats(user_scores):
    """Per-topic accuracy for a player's scores,the best topic first."""
    topic_stats = {}
    for s in user_scores:
        stats = topic_stats.setdefault(s.topic, {'total_score': 0, 'total_q': 0, 'count': 0, 'difficulty': s.difficulty})
        stats['total_score'] += s.score
        stats['total_q'] += s.total_questions
        stats['count'] += 1
        stats['difficulty'] = s.difficulty  # Scores come oldest first, so this ends as the latest difficulty

    analysis_data = [{
        'topic': topic,
        'avg_percentage': round(stats['total_score'] / stats['total_q'] * 100, 2),
        'games_played': stats['count'],
        'difficulty': stats['difficulty'],
    } for topic, stats in topic_stats.items()]
    analysis_data.sort(key=lambda x: x['avg_percentage'], reverse=True)
    return analysis_data


def find_practice_topic(user, analysis_data):
    """The player's weakest topic that a new quiz can be made for, with its saved custom content if any."""
    for data in reversed(analysis_data):
        if data['topic'] in ALL_TOPICS:
            return data, None
        content = (CustomContent.query.filter_by(user_id=user.id, title=data['topic'])
                   .order_by(CustomContent.created_at.desc()).first())
        if content:
            return data, content
    return None, None


def count_in_window(model, key, window, count_column, start_column):
    """Add one to `model`'s count for `key` and return the new count, starting over once `window` has passed since
    the window began. One atomic statement, so concurrent requests on any server instance are all counted."""
    now = utcnow()
    count, start = getattr(model, count_column), getattr(model, start_column)
    expired = start < now - window
    key_column = model.__table__.primary_key.columns[0].name
    insert = postgres_insert if db.engine.dialect.name == 'postgresql' else sqlite_insert
    result = db.session.execute(
        insert(model).values({key_column: key, count_column: 1, start_column: now})
        .on_conflict_do_update(index_elements=[key_column], set_={
            count_column: case((expired, 1), else_=count + 1),
            start_column: case((expired, now), else_=start),
        })
        .returning(count)
    ).scalar_one()
    db.session.commit()
    return result


def pin_lockout(key):
    """Count a PIN attempt for `key` and say whether it's locked out: None if the PIN may be checked, 'minutes'
    after MAX_PIN_ATTEMPTS in a PIN_LOCKOUT window, 'day' after PIN_DAILY_LIMIT in a day. The attempt is counted
    before the (deliberately slow) PIN check, so guesses sent in parallel can't all get past the limit; a correct
    PIN clears both counts. Attempts stopped by the short lockout don't count toward the daily one."""
    if count_in_window(PinAttempt, key, PIN_LOCKOUT, 'failures', 'first_failure') > MAX_PIN_ATTEMPTS:
        return 'minutes'
    if count_in_window(RateLimit, pin_day_key(key), PIN_DAY, 'hits', 'window_start') > PIN_DAILY_LIMIT:
        return 'day'
    return None


def pin_day_key(key):
    return f'pin-day:{key}'[:120]


def flash_pin_lockout(lockout):
    if lockout == 'day':
        flash(_("Too many wrong PINs for this name today. Please try again tomorrow."), "danger")
    else:
        flash(_("Too many wrong PINs for this name. Please wait a few minutes and try again."), "danger")


def client_ip():
    """The visitor's address. On Vercel it comes from X-Real-IP, which Vercel sets itself (a client can't)."""
    return (request.headers.get('X-Real-IP') if ON_VERCEL else None) or request.remote_addr or 'unknown'


def over_limit(kind, who):
    """Count a request of `kind` (a RATE_LIMITS key) by `who` and say whether it goes over the limit."""
    limit, window = RATE_LIMITS[kind]
    if secrets.randbelow(200) == 0:
        # Now and then, forget windows that ended long ago so the table doesn't keep every address forever
        RateLimit.query.filter(RateLimit.window_start < utcnow() - timedelta(days=1)).delete()
    return count_in_window(RateLimit, f'{kind}:{who}'[:120], window, 'hits', 'window_start') > limit


def quiz_generation_allowed(user):
    """Whether `user` may have another quiz made by the AI now; flashes why not."""
    if over_limit('quiz', user.id) or over_limit('quiz_ip', client_ip()):
        flash(_("You're going a bit fast. Please wait a few minutes and try again."), "warning")
        return False
    return True


def report_failure(message, error):
    """Tell the player what went wrong in their language. The details can name servers or SQL, so they only go to
    the log."""
    app.logger.error('%s failed: %r', request.path, error, exc_info=error)
    save_error(error)
    flash(message, "danger")


ERROR_LOG_SIZE = 500  # Newest errors kept in the error_log table


def describe_error(error):
    """'ValueError: message'. For database errors only the database's own message, without the SQL's values."""
    message = str(error.orig) if isinstance(error, StatementError) and error.orig else str(error)
    return f'{type(error).__name__}: {message}'[:2000]


def save_error(error):
    """Roll back the failed request's changes and store the error in error_log, dropping the oldest beyond
    ERROR_LOG_SIZE. Never raises: a broken error log mustn't hide the original error."""
    db.session.rollback()
    try:
        db.session.add(ErrorLog(method=request.method, path=request.path[:200], error=describe_error(error),
                                # The innermost frames matter most, so long tracebacks keep their end
                                traceback=''.join(traceback.format_tb(error.__traceback__))[-8000:]))
        db.session.flush()
        oldest_kept = (select(ErrorLog.id).order_by(ErrorLog.id.desc())
                       .offset(ERROR_LOG_SIZE - 1).limit(1).scalar_subquery())
        ErrorLog.query.filter(ErrorLog.id < oldest_kept).delete(synchronize_session=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error('Could not save the error: %r', e)


def flash_content_error(error):
    """A message for a ContentError, in the player's language."""
    if error.reason == 'unsupported_file':
        message = _("Invalid file type. Allowed types: PDF, TXT, DOCX")
    elif error.reason == 'scanned_pdf':
        message = _("We found little or no text in that PDF. If it's scanned pages (photos of text), try a PDF where you can select the text, a Word file, or paste the text instead.")
    elif error.reason == 'unreadable_file':
        message = _("We couldn't read that file. Please check that it isn't damaged or password-protected.")
    elif error.reason == 'blocked_url':
        message = _("That link can't be used. Please link to a public web page.")
    elif error.reason == 'too_short':
        message = _("There isn't enough text there to make a quiz. Please use a longer document or page.")
    else:
        message = _("We couldn't load that page. Check the link, or upload the text as a file instead.")
    app.logger.info('Custom content refused: %s', error)
    flash(message, "danger")


def clear_failed_pins(key):
    PinAttempt.query.filter_by(name=key).delete()
    RateLimit.query.filter_by(key=pin_day_key(key)).delete()
    db.session.commit()


@app.route('/')
def index():
    return render_template('index.html')


GUEST_ROUND_DAYS = 30  # Past daily challenges a guest round can come from


def pick_guest_round(language_name):
    """(daily challenge, round index) for a visitor to try without signing in: a round of a past daily challenge
    in their language (English if there's none), so it costs no AI call and spoils nothing about today's.
    None when no challenge has been played yet."""
    today, hidden = today_utc(), hidden_fingerprints()
    for language in dict.fromkeys([language_name, 'English']):
        challenges = (DailyChallenge.query.filter(DailyChallenge.language == language, DailyChallenge.date < today)
                      .order_by(DailyChallenge.date.desc()).limit(GUEST_ROUND_DAYS).all())
        # Every round of those days, except ones hidden after a report
        rounds = [(challenge, i) for challenge in challenges
                  for i, statements in enumerate(json.loads(challenge.questions_json))
                  if hidden_rounds.fingerprint(statements) not in hidden]
        if rounds:
            return random.choice(rounds)
    return None


def guest_round_row(challenge, index, picked):
    """The round as build_answer_review() shows it, so the answer review card can show the guest's result."""
    facts = json.loads(challenge.questions_json)[index]
    explanations = json.loads(challenge.explanations_json or '[]')
    quotes = json.loads(challenge.quotes_json or '[]')
    return {**review_round(facts, explanations[index] if index < len(explanations) else None,
                           quotes[index] if index < len(quotes) else None),
            'picked': picked, 'correct': facts.get(picked) is False, 'timed_out': False}


@app.route('/try', methods=['GET', 'POST'])
def try_round():
    """One round without signing in. The round is kept in the session (not in the page) so the answer is checked
    against the real one, and nothing is saved for the visitor."""
    held = session.get('guest_round')
    challenge = db.session.get(DailyChallenge, held[0]) if held else None
    if request.method == 'POST':
        if not challenge:
            return redirect(url_for('try_round'))
        index = held[1]
        picked = request.form.get('option')
        if picked not in json.loads(challenge.questions_json)[index]:
            return redirect(url_for('try_round'))
        session.pop('guest_round', None)
        return render_template('try.html', challenge=challenge, source_url=challenge.source_url,
                               review=[guest_round_row(challenge, index, picked)])

    if not challenge or request.args.get('new'):
        picked_round = pick_guest_round(LANGUAGE_OPTIONS.get(current_language(), 'English'))
        if not picked_round:
            flash(_("There are no rounds to try yet. Sign in and play the first game!"), "info")
            return redirect(url_for('index'))
        challenge, index = picked_round
        session['guest_round'] = [challenge.id, index]
    options = list(json.loads(challenge.questions_json)[session['guest_round'][1]])
    random.shuffle(options)
    return render_template('try.html', challenge=challenge, options=options)


def hidden_fingerprints():
    return {f for (f,) in db.session.query(HiddenRound.fingerprint)}


@app.route('/sw.js')
def service_worker():
    """The service worker (templates/sw.js), served from the site root so it may cover every page.
    Never cached, so a new version reaches players on their next visit. It lives in templates/, not public/:
    on Vercel public/ is only on the CDN and isn't part of the function, which made this route fail."""
    response = send_file(os.path.join(app.root_path, 'templates', 'sw.js'), mimetype='application/javascript',
                         max_age=0)
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@app.route('/offline')
def offline():
    """What the installed app shows without a connection (saved by the service worker)."""
    return render_template('offline.html')


@app.route('/logout')
def logout():
    # Also forgets the current quiz and help chat, so the next player on this browser starts fresh
    sign_out()
    return redirect(url_for('index'))


def delete_player_data(user, chat_id=None):
    """Remove a player and everything stored about them (shared daily-challenge quizzes stay)."""
    # Other players' challenge attempts against this player's quizzes are kept, without the link back
    own_quiz_ids = [q.id for q in QuizSession.query.filter_by(user_id=user.id).with_entities(QuizSession.id)]
    if own_quiz_ids:
        QuizSession.query.filter(QuizSession.challenge_of_id.in_(own_quiz_ids)).update(
            {QuizSession.challenge_of_id: None}, synchronize_session=False)
    # Rematches other players sent to this player stay in their history, addressed to nobody
    QuizSession.query.filter_by(rematch_for_id=user.id).update(
        {QuizSession.rematch_for_id: None}, synchronize_session=False)
    # Their live matches go too, except those someone else hosted: those stay, without them
    LiveMatch.query.filter_by(host_id=user.id).delete()
    LiveMatch.query.filter_by(guest_id=user.id).update({LiveMatch.guest_id: None}, synchronize_session=False)
    # Ready quizzes they were given stay counted toward the daily cap, without the link to their game
    if own_quiz_ids:
        ReadyQuiz.query.filter(ReadyQuiz.quiz_session_id.in_(own_quiz_ids)).update(
            {ReadyQuiz.quiz_session_id: None}, synchronize_session=False)
    fakes_views.delete_player_fakes(user)
    classroom_views.delete_rooms_of(user)
    league_views.forget_player(user)
    friend_views.forget_player(user)
    for model in (PushSubscription, RoundReport, UserBadge, PlayerAnalysis, Score, QuizSession, CustomContent):
        model.query.filter_by(user_id=user.id).delete()
    if chat_id:
        ChatbotSession.query.filter_by(session_id=chat_id).delete()
    db.session.delete(user)
    db.session.commit()


def check_account_pin(user, pin):
    """Verify a PIN for an account action, counting wrong guesses toward the sign-in lockout."""
    key = user.username.lower()
    lockout = pin_lockout(key)
    if lockout:
        flash_pin_lockout(lockout)
        return False
    if not user.pin_hash or not check_password_hash(user.pin_hash, pin):
        flash(_("That PIN isn't right."), "danger")
        return False
    clear_failed_pins(key)
    return True


@app.route('/account')
def account():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    counts = {
        'scores': Score.query.filter_by(user_id=user.id).count(),
        'uploads': CustomContent.query.filter_by(user_id=user.id).count(),
        'badges': UserBadge.query.filter_by(user_id=user.id).count(),
    }
    return render_template('account.html', user=user, counts=counts, categories=CATEGORIES,
                           max_name_length=MAX_NAME_LENGTH, wardrobe=finn_wardrobe(user),
                           max_file_bytes=MAX_FILE_SIZE, max_file_mb=MAX_FILE_MB, photo_max_side=PHOTO_MAX_SIDE)


def finn_wardrobe(user):
    """The account page's wardrobe: for each slot its items, with whether this player has unlocked each one, which
    badge unlocks it and how it looks (CSS variables), plus what they're wearing now."""
    badges = {b.badge for b in UserBadge.query.filter_by(user_id=user.id)}
    wearing = outfits.allowed(outfits.parse(user.finn_outfit), badges)
    slots = []
    for slot, items in outfits.SLOTS.items():
        slots.append({'key': slot, 'name': outfits.SLOT_NAMES[slot], 'items': [
            {'key': key, 'name': name, 'unlocked': outfits.unlocked(slot, key, badges),
             'badge': BADGES_BY_KEY[badge] if badge else None, 'colours': colours,
             'style': ';'.join(f'{k}:{v}' for k, v in colours.items()), 'worn': wearing[slot] == key}
            for key, name, badge, colours in items]})
    return {'slots': slots, 'style': outfits.style(wearing), 'extra': outfits.extra(wearing)}


@app.route('/account/finn', methods=['POST'])
def change_finn_outfit():
    """Save what the player's Finn wears; items they haven't unlocked fall back to the defaults."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    badges = {b.badge for b in UserBadge.query.filter_by(user_id=user.id)}
    choice = {slot: request.form.get(slot) for slot in outfits.SLOTS}
    user.finn_outfit = outfits.serialize(outfits.allowed(choice, badges)) or None
    db.session.commit()
    flash(_("Finn's new look is saved."), "success")
    return redirect(url_for('account', _anchor='finn'))


@app.route('/account/photo', methods=['POST'])
def change_photo():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    if request.form.get('remove'):
        user.avatar = user.avatar_updated_at = None
        db.session.commit()
        flash(_("Your photo has been removed."), "success")
        return redirect(url_for('account'))

    photo = request.files.get('photo')
    if not photo or not photo.filename:
        flash(_("Please choose a picture to upload."), "danger")
        return redirect(url_for('account'))
    image = make_avatar(photo.stream)
    if not image:
        flash(_("That file isn't a picture we can use. Please upload a JPEG, PNG, WebP or GIF image."), "danger")
        return redirect(url_for('account'))
    user.avatar = image
    user.avatar_updated_at = utcnow()
    db.session.commit()
    flash(_("Your new photo is saved."), "success")
    return redirect(url_for('account'))


@app.route('/account/topic', methods=['POST'])
def change_favorite_topic():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    topic = request.form.get('topic') or None
    if topic is None or topic in CATEGORIES.values():
        user.favorite_topic = topic
        db.session.commit()
        flash(_("Your favorite topic has been saved."), "success")
    return redirect(url_for('account'))


@app.route('/account/name', methods=['POST'])
def change_name():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    new_name = (request.form.get('new_name') or '').strip()
    if not check_account_pin(user, (request.form.get('pin') or '').strip()):
        return redirect(url_for('account'))
    problem = new_name_problem(new_name, user)
    if problem:
        flash(problem, "danger")
        return redirect(url_for('account'))
    if new_name != user.username:
        user.username = new_name
        try:
            db.session.commit()
        except IntegrityError:
            # Someone else took the name since the check
            db.session.rollback()
            flash(_("The name “{name}” is already taken. Please choose a different one.").format(name=new_name),
                  "danger")
            return redirect(url_for('account'))
        # Scores, quizzes, badges and sign-ins belong to the account, not the name, so nothing else changes
        flash(_("Your name is now “{name}”.").format(name=new_name), "success")
    return redirect(url_for('account'))


@app.route('/account/pin', methods=['POST'])
def change_pin():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    new_pin = (request.form.get('new_pin') or '').strip()
    if check_account_pin(user, (request.form.get('current_pin') or '').strip()):
        if not (len(new_pin) == 4 and new_pin.isdigit()):
            flash(_("Please enter a 4-digit PIN."), "danger")
        else:
            user.pin_hash = generate_password_hash(new_pin)
            db.session.commit()
            sign_in(user)  # This browser stays signed in; other devices are signed out
            flash(_("Your PIN has been changed."), "success")
    return redirect(url_for('account'))


@app.route('/account/delete', methods=['POST'])
def delete_account():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    if not request.form.get('confirm'):
        flash(_("Tick the box to confirm you want to delete your account."), "warning")
        return redirect(url_for('account'))
    if not check_account_pin(user, (request.form.get('pin') or '').strip()):
        return redirect(url_for('account'))

    name = user.username
    delete_player_data(user, chat_id=session.get('chat_id'))
    session.clear()
    flash(_("The account “{name}” and all its data have been deleted.").format(name=name), "success")
    return redirect(url_for('index'))


def print_errors(limit, full=False):
    """The newest saved errors, newest first."""
    errors = ErrorLog.query.order_by(ErrorLog.id.desc()).limit(limit).all()
    if not errors:
        click.echo('No errors saved.')
    for entry in errors:
        click.echo(f'{entry.created_at:%Y-%m-%d %H:%M} UTC  {entry.method} {entry.path}\n    {entry.error}')
        if full:
            click.echo(entry.traceback)


REPORT_REASONS = ('fake_is_true', 'real_is_false', 'other')


def reported_rounds(user, quiz_session):
    """Indexes of the rounds of `quiz_session` that `user` has already reported."""
    return {r.round_index for r in RoundReport.query.filter_by(user_id=user.id, quiz_session_id=quiz_session.id)}


@app.route('/report', methods=['POST'])
def report_round():
    """A player reports a round of one of their finished quizzes as wrong."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    back = local_path(request.form.get('next')) or url_for('history')
    quiz_session = db.session.get(QuizSession, request.form.get('quiz_session_id', type=int) or 0)
    index = request.form.get('round', type=int)
    reason = request.form.get('reason')
    if (not quiz_session or quiz_session.user_id != user.id or not quiz_session.is_completed or index is None
            or not 0 <= index < quiz_session.current_question_index or reason not in REPORT_REASONS):
        return redirect(back)
    if index in reported_rounds(user, quiz_session):
        flash(_("You've already reported this round."), "info")
        return redirect(back)
    if over_limit('report', user.id):
        flash(_("You're going a bit fast. Please wait a few minutes and try again."), "warning")
        return redirect(back)
    explanations = json.loads(quiz_session.explanations_json or '[]')
    statements = json.loads(quiz_session.questions_json)[index]
    db.session.add(RoundReport(
        user_id=user.id, quiz_session_id=quiz_session.id, round_index=index, topic=quiz_session.topic[:100],
        statements_json=json.dumps(statements), fingerprint=hidden_rounds.fingerprint(statements),
        explanation=explanations[index] if index < len(explanations) else None, source_url=quiz_session.source_url,
        reason=reason, comment=(request.form.get('comment') or '').strip()[:300] or None, created_at=utcnow()))
    db.session.commit()
    flash(_("Thanks! We'll check this round."), "success")
    return redirect(back)


RECENT_QUIZZES_FOR_DIFFICULTY = 5


def recommend_difficulty(user):
    """Suggest a difficulty from the player's last few quizzes: up a level after doing well, down after a
    rough patch. Returns {'id', 'direction', 'average', 'count'} or None until there are 3 quizzes."""
    if not user:
        return None
    recent = (Score.query.filter_by(user_id=user.id).order_by(Score.timestamp.desc(), Score.id.desc())
              .limit(RECENT_QUIZZES_FOR_DIFFICULTY).all())
    if len(recent) < 3:
        return None
    levels = list(DIFFICULTY_OPTIONS)  # '1' Easy, '2' Medium, '3' Hard
    current = next((key for key, label in DIFFICULTY_OPTIONS.items() if label == recent[0].difficulty), '2')
    average = sum(s.percentage for s in recent) / len(recent)
    index = levels.index(current)
    if average >= 80 and index < len(levels) - 1:
        index, direction = index + 1, 'up'
    elif average < 50 and index > 0:
        index, direction = index - 1, 'down'
    else:
        direction = 'stay'
    return {'id': levels[index], 'direction': direction, 'average': round(average), 'count': len(recent)}


@app.route('/topics/suggest')
def topic_suggestions():
    """Wikipedia's suggestions for the "Any topic" box on the setup page, as the player types."""
    user = current_user()
    if not user:
        abort(404)
    query = any_topic.clean_query(request.args.get('q'))
    if len(query) < any_topic.MIN_QUERY or over_limit('suggest', user.id):
        return jsonify(topics=[])
    return jsonify(topics=suggest_topics(query))


def wants_tour(user):
    """Whether the setup page starts Finn's tour: a player who hasn't seen it yet, or anyone who asked for it again
    (the footer's "Take the tour" link adds ?tour=1)."""
    return bool(user) and (user.tour_done_at is None or request.args.get('tour') == '1')


@app.route('/tour/done', methods=['POST'])
def tour_done():
    """The player finished or skipped the tour; don't start it on its own again, on any device."""
    user = current_user()
    if user and user.tour_done_at is None:
        user.tour_done_at = utcnow()
        db.session.commit()
    return ('', 204)


def render_setup():
    # A topic page's "Play a full quiz" link fills in the "Any topic" box (?topic=…)
    prefill = any_topic.clean_query(request.args.get('topic'))
    return render_template('setup.html', show_tour=wants_tour(current_user()), prefill_topic=prefill, categories=CATEGORIES,
                           lengths=GAME_LENGTH_OPTIONS,
                           difficulties=DIFFICULTY_OPTIONS, languages=LANGUAGE_OPTIONS,
                           daily_topic=daily_topic(today_utc()), event_theme=event_theme(),
                           timed_seconds=TIMED_ROUND_SECONDS,
                           recommended=recommend_difficulty(current_user()))


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        pin = (request.form.get('pin') or '').strip()
        if len(username) <= 2:
            flash(_("Please enter a valid name (at least 3 characters)."), "danger")
            return redirect(url_for('index'))
        if not (len(pin) == 4 and pin.isdigit()):
            flash(_("Please enter a 4-digit PIN."), "danger")
            return redirect(url_for('index'))

        # Names are matched case-insensitively so "phoenix" can't impersonate "Phoenix"
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        # The lockout follows the account a name leads to, so differently written forms of it share one count
        key = (user.username if user else username).lower()
        lockout = pin_lockout(key)
        if lockout:
            flash_pin_lockout(lockout)
            return redirect(url_for('index'))

        if not user:
            if len(username) > MAX_NAME_LENGTH:
                flash(new_name_problem(username), "danger")
                return redirect(url_for('index'))
            if over_limit('signup', client_ip()):
                flash(_("You're going a bit fast. Please wait a few minutes and try again."), "warning")
                return redirect(url_for('index'))
            user = User(username=username, pin_hash=generate_password_hash(pin))
            db.session.add(user)
            db.session.commit()
        elif not user.pin_hash:
            # Player from before PINs existed: the first PIN entered claims the name
            user.pin_hash = generate_password_hash(pin)
            db.session.commit()
        elif not check_password_hash(user.pin_hash, pin):
            flash(_('The name “{name}” is already taken. Enter its PIN, or choose a different name.').format(
                name=user.username), "danger")
            return redirect(url_for('index'))

        clear_failed_pins(key)
        next_url = local_path(session.pop('next_url', None))
        sign_in(user)

        # Back to the challenge (or other page) that asked the player to sign in
        if next_url:
            return redirect(next_url)
        return render_setup()

    if not current_user():
        return redirect(url_for('index'))
    
    return render_setup()


@app.route('/custom_quiz', methods=['GET', 'POST'])
def custom_quiz():
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    if request.method == 'POST':
        if not quiz_generation_allowed(user):
            return redirect(url_for('custom_quiz'))
        try:
            content = None
            source_info = None
            content_type = None
            saved_content = None

            # Check for a saved upload, a file upload, or a URL paste
            saved_id = request.form.get('saved_content_id', type=int)
            if saved_id:
                saved_content = CustomContent.query.filter_by(id=saved_id, user_id=user.id).first()
                if not saved_content:
                    flash(_("That upload isn't available anymore."), "danger")
                    return redirect(url_for('custom_quiz'))
                content = saved_content.content

            elif 'file' in request.files and request.files['file'].filename:
                file = request.files['file']

                if not allowed_file(file.filename):
                    flash(_("Invalid file type. Allowed types: PDF, TXT, DOCX"), "danger")
                    return redirect(url_for('custom_quiz'))

                try:
                    content = extract_text_from_upload(file.read(), file.filename)
                    source_info = secure_filename(file.filename)[:500]
                    content_type = 'file'
                except ContentError as e:
                    flash_content_error(e)
                    return redirect(url_for('custom_quiz'))

            elif (request.form.get('text') or '').strip():
                content = request.form['text'].strip()[:MAX_CONTENT_CHARS]
                source_info = None
                content_type = 'text'

            elif request.form.get('url'):
                url = request.form.get('url').strip()

                if not url:
                    flash(_("Please provide a valid URL"), "danger")
                    return redirect(url_for('custom_quiz'))

                try:
                    content = extract_text_from_url(url)
                    source_info = url[:500]
                    content_type = 'url'
                except ContentError as e:
                    flash_content_error(e)
                    return redirect(url_for('custom_quiz'))

            else:
                flash(_("Please upload a file, paste some text or a link."), "danger")
                return redirect(url_for('custom_quiz'))

            # Validate content length
            try:
                validate_content_length(content)
            except ContentError as e:
                flash_content_error(e)
                return redirect(url_for('custom_quiz'))

            # Store custom content in database, reusing the saved copy when the same content comes in again
            custom_content_obj = saved_content or CustomContent.query.filter_by(
                user_id=user.id, source=source_info, content=content).first()
            if not custom_content_obj:
                custom_content_obj = CustomContent(
                    user_id=user.id,
                    title=(request.form.get('title') or 'Untitled').strip()[:200],
                    content=content,
                    content_type=content_type,
                    source=source_info
                )
                db.session.add(custom_content_obj)
                db.session.commit()

            # Store only ID in session (content is already in database)
            session['custom_content_id'] = custom_content_obj.id

            # Get quiz parameters
            length_id = request.form.get('length')
            difficulty_id = request.form.get('difficulty')
            language_code = request.form.get('language', 'en')
            session['language'] = language_code

            if length_id not in GAME_LENGTH_OPTIONS or difficulty_id not in DIFFICULTY_OPTIONS:
                flash(_("Please select game length and difficulty."), "warning")
                return redirect(url_for('custom_quiz'))

            length_text = GAME_LENGTH_OPTIONS.get(length_id)
            questions_count = int(length_text.split("rounds")[0].split("(")[1].strip())
            difficulty_text = DIFFICULTY_OPTIONS.get(difficulty_id)

            try:
                # Generate quiz from custom content
                language_name = LANGUAGE_OPTIONS.get(language_code, "English")
                # A replayed upload keeps its topic unless the player typed a new title
                custom_title = (request.form.get('title') or '').strip()[:100] or (
                    saved_content.title if saved_content else None)
                quiz_session = create_quiz_session(user, "Custom Content", difficulty_text, questions_count,
                                                   language_name, custom_content=content,
                                                   custom_title=custom_title)

                # Save the content under the quiz topic so "Practice my weak spots" can reuse it
                custom_content_obj.title = quiz_session.topic
                db.session.commit()
                return redirect(url_for('quiz'))

            except Exception as e:
                report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
                return redirect(url_for('custom_quiz'))

        except Exception as e:
            report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
            return redirect(url_for('custom_quiz'))

    user = current_user()
    uploads = (CustomContent.query.filter_by(user_id=user.id).order_by(CustomContent.created_at.desc()).all()
               if user else [])
    return render_template('custom_quiz.html', lengths=GAME_LENGTH_OPTIONS, difficulties=DIFFICULTY_OPTIONS,
                           languages=LANGUAGE_OPTIONS, uploads=uploads, recommended=recommend_difficulty(user),
                           max_file_mb=MAX_FILE_MB, max_file_bytes=MAX_FILE_SIZE, max_text_chars=MAX_CONTENT_CHARS)


@app.route('/uploads/<int:content_id>/delete', methods=['POST'])
def delete_upload(content_id):
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    upload = CustomContent.query.filter_by(id=content_id, user_id=user.id).first()
    if upload:
        db.session.delete(upload)
        db.session.commit()
        flash(_('Deleted “{title}” from your uploads. Your past scores are kept.').format(title=upload.title),
              "success")
    return redirect(url_for('custom_quiz', tab='uploads'))


def quiz_settings_from_form():
    """(topic, difficulty, rounds, language name, category name, surprise?) chosen on the setup page, or None after
    saying what's missing. With "Any topic" the topic is the Wikipedia article the player's text resolves to and
    the category is None."""
    category_id = request.form.get('category')
    length_id = request.form.get('length')
    difficulty_id = request.form.get('difficulty')
    language_code = request.form.get('language', 'en')
    session['language'] = language_code

    if ((category_id not in CATEGORIES and category_id not in (SURPRISE, any_topic.ANY_TOPIC))
            or length_id not in GAME_LENGTH_OPTIONS or difficulty_id not in DIFFICULTY_OPTIONS):
        flash(_("Please select a category, game length, and difficulty."), "warning")
        return None

    if category_id == any_topic.ANY_TOPIC:
        # Only a real Wikipedia article is accepted, so the quiz has a source (any_topic.py)
        typed = any_topic.clean_query(request.form.get('topic'))
        user = current_user()
        if user and over_limit('topic', user.id):
            flash(_("You're going a bit fast. Please wait a few minutes and try again."), "warning")
            return None
        topic = resolve_topic(typed) if len(typed) >= any_topic.MIN_QUERY else None
        if not topic:
            flash(_("We couldn't find a Wikipedia article for that topic. Pick one of the suggestions."), "warning")
            return None
        # The AI rates who the topic is for; the difficulty says who is playing (any_topic.allowed)
        try:
            audience = check_topic_audience(topic)
        except Exception as error:
            save_error(error)
            flash(_("We couldn't check that topic right now. Please try again in a moment."), "warning")
            return None
        if not any_topic.allowed(audience, DIFFICULTY_OPTIONS.get(difficulty_id)):
            flash(_("This topic can't be played here. Try another one.") if audience == 'never' or audience not in any_topic.AUDIENCES
                  else _("This topic is for older players. Choose a harder level or another topic."), "warning")
            return None
        category_name = None
    else:
        # "Surprise me" picks a category first, so each one is as likely however long its list is
        category_name = random.choice(list(TOPICS)) if category_id == SURPRISE else CATEGORIES[category_id]
        topic = random.choice(TOPICS[category_name])
    length_text = GAME_LENGTH_OPTIONS.get(length_id)
    questions_count = int(length_text.split("rounds")[0].split("(")[1].strip())
    return (topic, DIFFICULTY_OPTIONS.get(difficulty_id), questions_count, LANGUAGE_OPTIONS.get(language_code, "English"),
            category_name, category_id == SURPRISE)


@app.route('/start_quiz', methods=['POST'])
def start_quiz():
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    settings = quiz_settings_from_form()
    if not settings or not quiz_generation_allowed(user):
        return redirect(url_for('setup'))
    topic, difficulty_text, questions_count, language_name, category, surprise = settings
    kind = ready_pool.Kind(category, difficulty_text, questions_count, language_name)
    if category:  # A topic the player typed has no category, so nothing is kept ready for it
        timers.note_demand(kind)

    try:
        time_limit = TIMED_ROUND_SECONDS if request.form.get('timed') else None
        # A quiz made ahead of time starts at once; without one, the AI makes it now (about 20 seconds)
        ready = timers.take_ready_quiz(kind, any_category=surprise) if category else None
        if ready:
            quiz_session = save_quiz_session(
                user, ready.topic, difficulty_text, json.loads(ready.questions_json),
                json.loads(ready.explanations_json), ready.source, time_limit=time_limit,
                quotes=json.loads(ready.quotes_json) if ready.quotes_json else None, source_url=ready.source_url)
            ready.quiz_session_id = quiz_session.id
            db.session.commit()
        else:
            create_quiz_session(user, topic, difficulty_text, questions_count, language_name, time_limit=time_limit)
        return redirect(url_for('quiz'))

    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('setup'))


def claim_round(quiz_session, round_index):
    """Move the quiz past `round_index` if it's still on it, in one conditional update, and say whether this
    request did. Of two answers sent for the same round at once, only one counts; the other changes nothing."""
    moved = db.session.execute(
        update(QuizSession)
        .where(QuizSession.id == quiz_session.id, QuizSession.current_question_index == round_index)
        .values(current_question_index=round_index + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if not moved:
        db.session.rollback()
        return False
    # Keep the loaded quiz in step without writing the index a second time
    set_committed_value(quiz_session, 'current_question_index', round_index + 1)
    return True


@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    if 'quiz_session_id' not in session:
        return redirect(url_for('setup'))
    
    quiz_session = db.session.get(QuizSession, session['quiz_session_id'])
    if not quiz_session or quiz_session.is_completed:
        return redirect(url_for('setup'))
    
    questions = json.loads(quiz_session.questions_json)

    if request.method == 'POST':
        selected_option = request.form.get('option')
        round_index = quiz_session.current_question_index
        current_q = questions[round_index]
        # The quiz page sends answers in the background and shows the result on the round itself (the FAKE stamp);
        # it gets JSON back. Without JavaScript the form posts as before and the result is a message on the next page
        reveal = request.headers.get('X-Requested-With') == 'fetch'

        def answered(endpoint, **result):
            return jsonify(next=url_for(endpoint), **result) if reveal else redirect(url_for(endpoint))
        # Timed mode: the browser submits when the countdown ends; the server also checks the clock, so a late
        # answer (or one sent with JavaScript turned off) can't beat the timer
        timed_out = bool(quiz_session.time_limit) and (
            request.form.get('timeout') == '1' or seconds_left(quiz_session) <= -TIMER_GRACE_SECONDS)
        if not selected_option and not timed_out:
            if reveal:
                return jsonify(error=_("Please select an option.")), 400
            flash(_("Please select an option."), "warning")
        elif not claim_round(quiz_session, round_index):
            # Another request (a double click, or the timer firing during a click) already answered this round
            return answered('quiz')
        else:
            pick = None if timed_out else selected_option
            # The options were shuffled in the template, we need to check the value
            is_correct = pick is not None and current_q.get(pick) is False  # False means it's the fake one

            # Remember the pick for the answer review on the results page (None = time ran out)
            answers = json.loads(quiz_session.answers_json or '[]')
            answers.append(pick)
            quiz_session.answers_json = json.dumps(answers)

            correct_answer = next(k for k, v in current_q.items() if v is False)
            if is_correct:
                quiz_session.score += 1
                message, category = _("Correct! You found the fake fact."), "success"
            elif timed_out:
                message, category = _("Time's up! The fake fact was:") + " " + correct_answer, "danger"
            else:
                message, category = _("Incorrect. The fake fact was:") + " " + correct_answer, "danger"
            if not reveal:
                flash(message, category)
            result = {'correct': is_correct, 'timed_out': timed_out, 'fake': correct_answer, 'message': message}

            quiz_session.round_started_at = None

            if quiz_session.current_question_index >= quiz_session.total_questions:
                quiz_session.is_completed = True
                if quiz_session.practice:
                    # Practice earns no score, badges or leaderboard points: the player had seen the answers
                    db.session.commit()
                    return answered('results', **result)

                # Save final score
                percentage = (quiz_session.score / quiz_session.total_questions) * 100
                final_score = Score(
                    user_id=quiz_session.user_id,
                    topic=quiz_session.topic,
                    difficulty=quiz_session.difficulty,
                    score=quiz_session.score,
                    total_questions=quiz_session.total_questions,
                    percentage=percentage,
                    quiz_session_id=quiz_session.id
                )
                db.session.add(final_score)
                db.session.commit()

                player = db.session.get(User, quiz_session.user_id)
                league_views.join_league(player)  # The first finished game of the week puts them in a league
                flash_new_badges(player)
                return answered('results', **result)

            db.session.commit()
            return answered('quiz', **result)

    # The round's clock starts the first time it's shown; reloading the page doesn't restart it
    if quiz_session.time_limit and quiz_session.round_started_at is None:
        quiz_session.round_started_at = utcnow()
        db.session.commit()

    # Prepare current question
    current_q = questions[quiz_session.current_question_index]
    options = list(current_q.keys())
    random.shuffle(options)
    
    return render_template('quiz.html', 
                           question_num=quiz_session.current_question_index + 1,
                           total_questions=quiz_session.total_questions,
                           topic=_('Practice') if quiz_session.practice else quiz_session.topic,
                           options=options,
                           time_limit=quiz_session.time_limit,
                           seconds_left=max(0, round(seconds_left(quiz_session))) if quiz_session.time_limit else None,
                           source=session.get('quiz_source', 'Wikipedia'))


@app.route('/results')
def results():
    if 'quiz_session_id' not in session:
        return redirect(url_for('setup'))
    
    quiz_session = db.session.get(QuizSession, session['quiz_session_id'])
    if not quiz_session:
        return redirect(url_for('setup'))
    if not quiz_session.is_completed:
        return redirect(url_for('quiz'))

    percentage = (quiz_session.score / quiz_session.total_questions) * 100
    opponent = challenge_opponent(quiz_session)
    is_practice = bool(quiz_session.practice)
    # Only the player who played the quiz can report its rounds
    user = current_user()
    owner = user if user and user.id == quiz_session.user_id else None

    return render_template('results.html',
                           score=quiz_session.score,
                           total=quiz_session.total_questions,
                           percentage=round(percentage, 2),
                           topic=quiz_session.topic,
                           is_daily=quiz_session.daily_challenge_id is not None,
                           is_event=quiz_session.event_week is not None,
                           share=None if is_practice else share_details(quiz_session),
                           challenge=challenge_comparison(quiz_session),
                           is_practice=is_practice, case_no=quiz_session.id,
                           league_points=None if is_practice else leagues.points_for(quiz_session.difficulty,
                                                                                    quiz_session.score),
                           practice_left=len(practice_pool(user)) if user else 0,
                           review=build_answer_review(quiz_session, opponent),
                           versus=versus_details(quiz_session, opponent),
                           source_url=quiz_session.source_url,
                           report_quiz=quiz_session if owner else None,
                           reported=reported_rounds(owner, quiz_session) if owner else set(),
                           rematch_for=db.session.get(User, quiz_session.rematch_for_id)
                           if quiz_session.rematch_for_id else None)


@app.route('/r/<code>/card.png')
def shared_result_card(code):
    """The link-preview image of a shared result. A finished result never changes, so it's cached for a day."""
    quiz_session = QuizSession.query.filter_by(share_code=code, is_completed=True).first()
    if not quiz_session:
        abort(404)
    player = db.session.get(User, quiz_session.user_id)
    png = result_card(player.username if player else '', quiz_session.score, quiz_session.total_questions,
                      quiz_session.topic)
    response = app.response_class(png, mimetype='image/png')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@app.route('/r/<code>')
def shared_result(code):
    """Public card for a finished quiz, reachable only through the link its player shared."""
    quiz_session = QuizSession.query.filter_by(share_code=code, is_completed=True).first()
    if not quiz_session:
        abort(404)
    player = db.session.get(User, quiz_session.user_id)
    score = Score.query.filter_by(quiz_session_id=quiz_session.id).first()
    viewer = current_user()
    my_attempt = (QuizSession.query.filter_by(user_id=viewer.id, challenge_of_id=quiz_session.id).first()
                  if viewer else None)
    challengers = (db.session.query(QuizSession, User).join(User, QuizSession.user_id == User.id)
                   .filter(QuizSession.challenge_of_id == quiz_session.id, QuizSession.is_completed.is_(True))
                   .order_by(QuizSession.score.desc(), QuizSession.id).all())
    return render_template('shared.html', quiz_session=quiz_session, player=player, score=score,
                           percentage=round(quiz_session.score / quiz_session.total_questions * 100),
                           is_owner=viewer is not None and viewer.id == player.id, my_attempt=my_attempt,
                           challengers=challengers)


@app.route('/r/<code>/play', methods=['POST'])
def take_challenge(code):
    """Play the exact questions of a shared quiz and compare scores."""
    original = QuizSession.query.filter_by(share_code=code, is_completed=True).first()
    if not original:
        abort(404)
    user = current_user()
    if not user:
        # Come back here after signing in
        session['next_url'] = url_for('shared_result', code=code)
        flash(_("Sign in to take the challenge."), "info")
        return redirect(url_for('index'))
    if original.daily_challenge_id:
        # Replaying a daily challenge would reveal today's questions
        flash(_("This was a daily challenge. Play today's daily challenge instead!"), "info")
        return redirect(url_for('daily'))
    if original.user_id == user.id:
        flash(_("This is your own quiz, so here are your answers instead."), "info")
        return redirect(url_for('review', quiz_session_id=original.id))

    attempt = QuizSession.query.filter_by(user_id=user.id, challenge_of_id=original.id).first()
    if attempt and attempt.is_completed:
        flash(_("You've already taken this challenge."), "info")
        return redirect(url_for('shared_result', code=code))
    if attempt:
        session['quiz_session_id'] = attempt.id
        return redirect(url_for('quiz'))
    # Both players' answers are compared round by round, so a replay can't just leave a wrong round out
    if hidden_rounds.has_hidden(json.loads(original.questions_json), hidden_fingerprints()):
        flash(_("A round of this quiz turned out to be wrong, so it can't be replayed. Try a new quiz instead!"), "info")
        return redirect(url_for('setup'))

    save_quiz_session(user, original.topic, original.difficulty, json.loads(original.questions_json),
                      json.loads(original.explanations_json or '[]'), _('Challenge'),
                      time_limit=original.time_limit, challenge_of_id=original.id,
                      quotes=json.loads(original.quotes_json) if original.quotes_json else None,
                      source_url=original.source_url)
    return redirect(url_for('quiz'))


@app.route('/daily')
def daily():
    date = today_utc()
    user = current_user()
    attempt = daily_attempt(user, date) if user else None
    attempt_score = Score.query.filter_by(quiz_session_id=attempt.id).first() if attempt else None

    # Today's standings across every language: most correct first, earliest finish breaks ties
    standings = (db.session.query(Score, User)
                 .join(User, Score.user_id == User.id)
                 .join(QuizSession, Score.quiz_session_id == QuizSession.id)
                 .join(DailyChallenge, QuizSession.daily_challenge_id == DailyChallenge.id)
                 .filter(DailyChallenge.date == date)
                 .order_by(Score.score.desc(), Score.timestamp)
                 .all())
    return render_template('daily.html', topic=daily_topic(date), date=date, attempt=attempt,
                           attempt_score=attempt_score, standings=standings, rounds=DAILY_ROUNDS,
                           difficulty=DAILY_DIFFICULTY, push_key=timers.push_ready() and app.config['VAPID_PUBLIC_KEY'])


@app.route('/daily/start', methods=['POST'])
def daily_start():
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    date = today_utc()
    attempt = daily_attempt(user, date)
    if attempt and attempt.is_completed:
        flash(_("You've already played today's challenge. Come back tomorrow for a new one!"), "info")
        return redirect(url_for('daily'))
    if attempt:
        # Resume an unfinished attempt instead of starting over
        session['quiz_session_id'] = attempt.id
        return redirect(url_for('quiz'))

    try:
        language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
        challenge = get_daily_challenge(date, language_name)
        # A round hidden after a report is left out, so later players get a shorter quiz instead of a wrong round
        rounds, explanations, quotes = hidden_rounds.visible_rounds(
            json.loads(challenge.questions_json), json.loads(challenge.explanations_json),
            json.loads(challenge.quotes_json) if challenge.quotes_json else None, hidden_fingerprints())
        if not rounds:
            raise ValueError("Every round of today's daily challenge was hidden")
        save_quiz_session(user, challenge.topic, DAILY_DIFFICULTY, rounds, explanations, challenge.source,
                          daily_challenge_id=challenge.id, quotes=quotes, source_url=challenge.source_url)
        return redirect(url_for('quiz'))
    except IntegrityError:
        # A second click started the same attempt at the same moment; continue that one
        db.session.rollback()
        attempt = daily_attempt(user, date)
        if attempt:
            session['quiz_session_id'] = attempt.id
        return redirect(url_for('quiz'))
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('daily'))


@app.route('/event')
def event():
    today = utcnow().date()
    week = event_week(today)
    standings = event_standings(week)
    user = current_user()
    my_row = next((row for row in standings if user and row['user'].id == user.id), None)
    last_week_day = today - timedelta(days=7)
    last_standings = event_standings(event_week(last_week_day))
    return render_template('event.html', theme=event_theme(today), week_start=week_start(today),
                           days_left=6 - today.weekday(), standings=standings, my_row=my_row,
                           last_theme=event_theme(last_week_day),
                           last_champion=last_standings[0] if last_standings else None,
                           rounds=EVENT_ROUNDS, difficulty=EVENT_DIFFICULTY, best_of=EVENT_BEST_OF,
                           max_points=EVENT_BEST_OF * EVENT_ROUNDS)


@app.route('/event/start', methods=['POST'])
def event_start():
    user = current_user()
    if not user:
        session['next_url'] = url_for('event')
        flash(_("Sign in to play this week's event."), "info")
        return redirect(url_for('index'))
    if not quiz_generation_allowed(user):
        return redirect(url_for('event'))

    week, theme = event_week(), event_theme()
    try:
        language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
        create_quiz_session(user, event_topic(user, week, theme), EVENT_DIFFICULTY, EVENT_ROUNDS, language_name,
                            event_week=week)
        return redirect(url_for('quiz'))
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('event'))


@app.route('/history')
def history():
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    scores = Score.query.filter_by(user_id=user.id).order_by(Score.timestamp).all()
    chart_points = [{
        'n': i + 1,
        'date': format_date(s.timestamp, with_time=True),
        'topic': s.topic,
        'pct': round(s.percentage),
        'score': s.score,
        'total': s.total_questions,
    } for i, s in enumerate(scores)]
    return render_template('history.html', scores=list(reversed(scores)), chart_points=chart_points,
                           practice_left=len(practice_pool(user)))


# ---------- Practice your mistakes (rules in practice.py) ----------------------------------------------------------

def practice_pool(user, with_details=False):
    """The rounds `user` still has to practice (see practice.mistakes). Pages that only show how many read just
    the rounds and answers; starting a practice quiz (with_details) also needs the explanations and quotes."""
    columns = [QuizSession.practice, QuizSession.questions_json, QuizSession.answers_json]
    if with_details:
        columns += [QuizSession.explanations_json, QuizSession.quotes_json]
    rows = (db.session.query(*columns).filter(QuizSession.user_id == user.id, QuizSession.is_completed.is_(True))
            .order_by(QuizSession.id.desc()).limit(practice_rules.GAMES_LOOKED_AT).all())

    def as_game(row):
        details = (json.loads(row[3] or '[]'), json.loads(row[4] or '[]')) if with_details else (None, None)
        return (json.loads(row[1]), json.loads(row[2] or '[]')) + details
    games = [as_game(r) for r in rows if not r[0]]
    practice_games = [as_game(r) for r in rows if r[0]]
    return practice_rules.mistakes(games, practice_games, hidden_fingerprints())


@app.route('/practice/mistakes', methods=['POST'])
def practice_mistakes():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    found = practice_pool(user, with_details=True)
    if not found:
        flash(_("No mistakes to practice right now. Nice work!"), "success")
        return redirect(url_for('history'))
    rounds, explanations, quotes = practice_rules.choose(found)
    save_quiz_session(user, 'Practice', 'Practice', rounds, explanations, _('Your mistakes'), quotes=quotes,
                      is_practice=True)
    return redirect(url_for('quiz'))


@app.route('/review/<int:quiz_session_id>')
def review(quiz_session_id):
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    quiz_session = db.session.get(QuizSession, quiz_session_id)
    # Reviews are private like the analysis; only finished quizzes can be reviewed
    if not quiz_session or quiz_session.user_id != user.id or not quiz_session.is_completed:
        flash(_("That quiz review isn't available."), "warning")
        return redirect(url_for('history'))

    score = Score.query.filter_by(quiz_session_id=quiz_session.id).first()
    # A challenge attempt is compared with the quiz it replayed; the original quiz with one challenger (?vs=)
    opponent = challenge_opponent(quiz_session, request.args.get('vs', type=int))
    return render_template('review.html', quiz_session=quiz_session, score=score,
                           share=share_details(quiz_session), review=build_answer_review(quiz_session, opponent),
                           versus=versus_details(quiz_session, opponent), source_url=quiz_session.source_url,
                           report_quiz=quiz_session, reported=reported_rounds(user, quiz_session))


@app.route('/challenges')
def challenges():
    """Who took the player's challenges (newest first, the ones since their last visit marked new) and the
    challenges they took themselves."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    seen_at = user.challenges_seen_at
    received = [{'attempt': attempt, 'challenger': challenger, 'original': original,
                 'is_new': seen_at is None or score.timestamp > seen_at}
                for attempt, score, challenger, original
                in challengers_query(user).order_by(Score.timestamp.desc(), Score.id.desc()).all()]
    original = aliased(QuizSession)
    taken = (db.session.query(QuizSession, original, User)
             .join(original, QuizSession.challenge_of_id == original.id)
             .join(User, original.user_id == User.id)
             .filter(QuizSession.user_id == user.id, QuizSession.is_completed.is_(True))
             .order_by(QuizSession.id.desc()).all())

    rematches = [{'quiz': quiz, 'sender': sender, 'is_new': seen_at is None or score.timestamp > seen_at}
                 for quiz, score, sender
                 in pending_rematches_query(user).order_by(Score.timestamp.desc(), Score.id.desc()).all()]

    user.challenges_seen_at = utcnow()
    db.session.commit()
    return render_template('challenges.html', received=received, taken=taken, rematches=rematches,
                           can_rematch=can_rematch)


@app.route('/rematch/<int:attempt_id>', methods=['POST'])
def rematch(attempt_id):
    """Play a new quiz on the topic of a finished challenge; when it's done, it's sent to the other player."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    attempt = db.session.get(QuizSession, attempt_id)
    original = db.session.get(QuizSession, attempt.challenge_of_id) if attempt and attempt.challenge_of_id else None
    if not original or not attempt.is_completed or user.id not in (attempt.user_id, original.user_id):
        flash(_("That rematch isn't available."), "warning")
        return redirect(url_for('challenges'))
    if not can_rematch(attempt):
        flash(_("Rematches are only possible for quizzes on the built-in topics."), "info")
        return redirect(url_for('challenges'))
    if not quiz_generation_allowed(user):
        return redirect(url_for('challenges'))

    opponent_id = original.user_id if user.id == attempt.user_id else attempt.user_id
    try:
        language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
        create_quiz_session(user, original.topic, original.difficulty, original.total_questions, language_name,
                            time_limit=original.time_limit, rematch_for_id=opponent_id)
        return redirect(url_for('quiz'))
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('challenges'))


LEADERBOARD_SIZE = 10


@app.route('/leaderboard')
def leaderboard():
    # Filters: unknown values fall back to "all"
    period = request.args.get('period', 'all')
    category = request.args.get('category', 'all')
    difficulty = request.args.get('difficulty', 'all')
    period = period if period in LEADERBOARD_PERIODS else 'all'
    category = category if category in CATEGORIES or category == 'custom' else 'all'
    difficulty = difficulty if difficulty in DIFFICULTY_OPTIONS else 'all'

    conditions = []
    if period == 'today':
        conditions.append(Score.timestamp >= utcnow().replace(hour=0, minute=0, second=0, microsecond=0))
    elif period == 'week':
        conditions.append(Score.timestamp >= utcnow() - timedelta(days=7))
    if category == 'custom':
        # Anything that isn't a built-in topic came from a custom quiz
        conditions.append(Score.topic.notin_(ALL_TOPICS))
    elif category != 'all':
        conditions.append(Score.topic.in_(TOPICS[CATEGORIES[category]]))
    if difficulty != 'all':
        conditions.append(Score.difficulty == DIFFICULTY_OPTIONS[difficulty])

    # Ranked by rating (rating.py): accuracy weighted by difficulty, cautious for players with few answers.
    # One row per player and difficulty from the database; the rating itself is worked out here.
    rows = (db.session.query(User, Score.difficulty, func.sum(Score.score), func.sum(Score.total_questions),
                             func.count(Score.id))
            .join(Score, Score.user_id == User.id)
            .filter(*conditions)
            .group_by(User.id, Score.difficulty)
            .all())
    players = {}
    for user, difficulty_label, correct, questions, quizzes in rows:
        player = players.setdefault(user.id, {'user': user, 'groups': [], 'quizzes': 0})
        player['groups'].append((difficulty_label, correct or 0, questions or 0))
        player['quizzes'] += quizzes
    standings = rank_players(list(players.values()))[:LEADERBOARD_SIZE]
    filters = {'period': period, 'category': category, 'difficulty': difficulty}
    return render_template('leaderboard.html', standings=standings, filters=filters,
                           filtered=any(v != 'all' for v in filters.values()),
                           periods=LEADERBOARD_PERIODS, categories=CATEGORIES, difficulties=DIFFICULTY_OPTIONS)


@app.route('/players')
def players():
    # Everyone who has played, with their totals, in a few queries however many players there are
    rows = (db.session.query(User, func.count(Score.id), func.avg(Score.percentage))
            .join(Score, Score.user_id == User.id).group_by(User.id).all())
    badges = dict(db.session.query(UserBadge.user_id, func.count(UserBadge.id)).group_by(UserBadge.user_id).all())
    play_days = {}
    for user_id, timestamp in db.session.query(Score.user_id, Score.timestamp):
        if timestamp:
            play_days.setdefault(user_id, []).append(timestamp.date())
    players_data = [{
        'user': user,
        'username': user.username,
        'games_played': games,
        'avg_score': round(average, 2),
        'streak': compute_streaks(play_days.get(user.id, []))[0],
        'badges': badges.get(user.id, 0),
    } for user, games, average in rows]
    
    # Sort by games played or avg score
    players_data.sort(key=lambda x: x['games_played'], reverse=True)
    return render_template('players.html', players=players_data)


@app.route('/players/<int:user_id>')
def player_profile(user_id):
    """A player's public profile: photo, favorite topic, stats and badges (their AI analysis stays private)."""
    player = db.get_or_404(User, user_id)
    scores = Score.query.filter_by(user_id=player.id).all()
    topic_stats = get_topic_stats(scores)
    current_streak, best_streak = compute_streaks([s.timestamp.date() for s in scores if s.timestamp])
    earned = {b.badge: b.earned_at for b in UserBadge.query.filter_by(user_id=player.id)}
    return render_template('profile.html', player=player,
                           games_played=len(scores),
                           avg_score=sum(s.percentage for s in scores) / len(scores) if scores else None,
                           best_topic=topic_stats[0] if topic_stats else None,
                           current_streak=current_streak, best_streak=best_streak,
                           earned_badges=[(badge, earned[badge['key']]) for badge in BADGES if badge['key'] in earned],
                           total_badges=len(BADGES),
                           is_me=current_user() is not None and current_user().id == player.id)


@app.route('/players/<int:user_id>/avatar')
def player_avatar(user_id):
    player = db.get_or_404(User, user_id)
    if not player.avatar_updated_at:
        abort(404)
    # Links to the photo carry ?v=<when it was uploaded>, so browsers can keep it until it changes. Private, so
    # a CDN (like Vercel's) doesn't keep serving a photo after it's removed or the account is deleted.
    response = send_file(io.BytesIO(player.avatar), mimetype='image/webp')
    response.headers['Cache-Control'] = f'private, max-age={365 * 24 * 3600}'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route('/analysis')
@app.route('/analysis/<int:user_id>')
def analysis(user_id=None):
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    # Performance analysis is private: players can only open their own
    if user_id is not None and user_id != user.id:
        flash(_("Performance analysis is private. You can only view your own."), "warning")
        return redirect(url_for('analysis'))

    user_scores = Score.query.filter_by(user_id=user.id).order_by(Score.id).all()

    if not user_scores:
        flash(_("Play some games first to see your performance analysis!"), "info")
        return redirect(url_for('setup'))

    analysis_data = get_topic_stats(user_scores)
    practice_topic, _content = find_practice_topic(user, analysis_data)

    # Reuse the saved analysis unless there are new scores or the language changed
    language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
    latest_score_id = user_scores[-1].id
    saved = PlayerAnalysis.query.filter_by(user_id=user.id).first()
    games_to_coaching = max(MIN_COACHING_GAMES - len(user_scores), 0)
    if games_to_coaching:
        ai_analysis = None  # The AI coach waits for enough games; no call until then
    elif saved and saved.last_score_id == latest_score_id and saved.language == language_name:
        ai_analysis = AIAnalysis.model_validate_json(saved.analysis_json)
    else:
        try:
            ai_analysis = get_ai_analysis(json.dumps(coaching_history(user_scores)), language_name)
            if not saved:
                saved = PlayerAnalysis(user_id=user.id)
                db.session.add(saved)
            saved.language = language_name
            saved.last_score_id = latest_score_id
            saved.analysis_json = ai_analysis.model_dump_json()
            saved.created_at = utcnow()
            db.session.commit()
        except Exception as e:
            app.logger.error('AI analysis failed: %r', e, exc_info=e)
            save_error(e)
            # Fall back to the last saved analysis, even if it's missing the newest quizzes
            ai_analysis = AIAnalysis.model_validate_json(saved.analysis_json) if saved else None

    # Players who finished quizzes before badges existed get theirs on their next visit
    flash_new_badges(user)
    earned = {b.badge: b.earned_at for b in UserBadge.query.filter_by(user_id=user.id)}
    current_streak, best_streak = player_streaks(user)

    return render_template('analysis.html',
                           analysis_data=analysis_data,
                           ai_analysis=ai_analysis,
                           games_to_coaching=games_to_coaching,
                           practice_topic=practice_topic,
                           badges=BADGES, earned_badges=earned,
                           current_streak=current_streak, best_streak=best_streak,
                           played_today=user_scores[-1].timestamp.date() == utcnow().date(),
                           profile_user=user)


@app.route('/practice', methods=['POST'])
def practice():
    """Start a quiz on the player's weakest topic."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    user_scores = Score.query.filter_by(user_id=user.id).order_by(Score.id).all()
    practice_topic, custom_content = find_practice_topic(user, get_topic_stats(user_scores))
    if not practice_topic:
        flash(_("Play a few quizzes first so we can find a topic to practice."), "info")
        return redirect(url_for('setup'))

    language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
    questions_count = 5  # Medium length
    if not quiz_generation_allowed(user):
        return redirect(url_for('analysis'))
    try:
        if custom_content:
            create_quiz_session(user, "Custom Content", practice_topic['difficulty'], questions_count, language_name,
                                custom_content=custom_content.content, custom_title=practice_topic['topic'])
        else:
            create_quiz_session(user, practice_topic['topic'], practice_topic['difficulty'], questions_count,
                                language_name)
        return redirect(url_for('quiz'))
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('analysis'))


if __name__ == '__main__':
    # Development server. FLASK_DEBUG=1 turns on auto-reload and the in-browser debugger, which can run code,
    # so it's off unless asked for. For anything other people use, run serve.py instead.
    with app.app_context():
        init_database()
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=int(os.environ.get('PORT', 5003)))


# Routes that live in their own modules register on `app` when imported, so this stays at the end of the file
from views import admin as admin_views, live as live_views, timers, fakes as fakes_views  # noqa: E402,F401
from views import classroom as classroom_views  # noqa: E402
from views import leagues as league_views  # noqa: E402
from views import topics as topic_views  # noqa: E402,F401
from views import friends as friend_views  # noqa: E402
from views import embed as embed_views  # noqa: E402,F401
from views import help_chat  # noqa: E402,F401
