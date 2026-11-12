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
                    UserBadge, PinAttempt, RateLimit, ErrorLog, LiveMatch, RoundReport)
from data import get_ai_response, get_ai_analysis, AIAnalysis
from default_text import CATEGORIES, MENU, TOPICS, ALL_TOPICS, SURPRISE, GAME_LENGTH_OPTIONS, DIFFICULTY_OPTIONS, LANGUAGE_OPTIONS, LEADERBOARD_PERIODS
from translations import (UI_LANGUAGES, current_language, format_date, template_context, template_globals, translate as _,
                          translate_plural as _n)
from content_extractor import (ContentError, extract_text_from_upload, extract_text_from_url,
                               validate_content_length)
from chatbot import QUICK_HELP, get_chatbot_response
from badges import BADGES, award_badges, compute_streaks, player_streaks
import live
from rating import rank as rank_players
from og_card import result_card


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

# File upload configuration (uploads are read in memory, never saved)
ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Profile photos: re-encoded as square WebP images of this size and stored in the database
AVATAR_SIZE = 256
AVATAR_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}
AVATAR_MAX_PIXELS = 40_000_000  # Bigger pictures are refused before they're decoded

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
    return {'me': me, 'challenge_news': count_challenge_news(me) if me else 0,
            'donation_url': donation_url if donation_url.startswith('https://') else None}


app.jinja_env.globals.update(template_globals())
app.add_template_filter(format_date, 'date')
app.add_template_filter(lambda value: format_date(value, with_time=True), 'datetime')


@app.after_request
def security_headers(response):
    # No framing by other sites (clickjacking), no guessing content types, and only the origin in cross-site
    # referrers so share codes and quiz pages don't leak to links people follow
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
    flash(_("That file is too big. The limit is 10 MB."), "danger")
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
                      event_week=None):
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
        row = {'facts': list(facts.items()), **outcome(answers, i, facts),
               'explanation': explanations[i] if i < len(explanations) else None,
               'quote': quotes[i] if i < len(quotes) else None}
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
    today = today_utc()
    for language in dict.fromkeys([language_name, 'English']):
        challenges = (DailyChallenge.query.filter(DailyChallenge.language == language, DailyChallenge.date < today)
                      .order_by(DailyChallenge.date.desc()).limit(GUEST_ROUND_DAYS).all())
        if challenges:
            challenge = random.choice(challenges)
            return challenge, random.randrange(len(json.loads(challenge.questions_json)))
    return None


def guest_round_row(challenge, index, picked):
    """The round as build_answer_review() shows it, so the answer review card can show the guest's result."""
    facts = json.loads(challenge.questions_json)[index]
    explanations = json.loads(challenge.explanations_json or '[]')
    quotes = json.loads(challenge.quotes_json or '[]')
    return {'facts': list(facts.items()), 'picked': picked, 'correct': facts.get(picked) is False,
            'timed_out': False, 'explanation': explanations[index] if index < len(explanations) else None,
            'quote': quotes[index] if index < len(quotes) else None}


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


@app.route('/sw.js')
def service_worker():
    """The service worker (public/static/js/sw.js), served from the site root so it may cover every page.
    Never cached, so a new version reaches players on their next visit."""
    response = send_file(os.path.join(app.static_folder, 'js', 'sw.js'), mimetype='application/javascript',
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
    for model in (RoundReport, UserBadge, PlayerAnalysis, Score, QuizSession, CustomContent):
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
                           max_name_length=MAX_NAME_LENGTH)


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
    db.session.add(RoundReport(
        user_id=user.id, quiz_session_id=quiz_session.id, round_index=index, topic=quiz_session.topic[:100],
        statements_json=json.dumps(json.loads(quiz_session.questions_json)[index]),
        explanation=explanations[index] if index < len(explanations) else None, source_url=quiz_session.source_url,
        reason=reason, comment=(request.form.get('comment') or '').strip()[:300] or None, created_at=utcnow()))
    db.session.commit()
    flash(_("Thanks! We'll check this round."), "success")
    return redirect(back)


@app.cli.command('reports')
@click.option('-n', '--limit', default=20, show_default=True, help='How many to show.')
def reports_command(limit):
    """Show the newest rounds players reported as wrong."""
    reports = RoundReport.query.order_by(RoundReport.id.desc()).limit(limit).all()
    if not reports:
        click.echo('No reports.')
    for r in reports:
        user = db.session.get(User, r.user_id)
        facts = json.loads(r.statements_json)
        fake = next((s for s, real in facts.items() if real is False), '?')
        click.echo(f'{r.created_at:%Y-%m-%d %H:%M} UTC  {user.username if user else "?"}  [{r.reason}]  {r.topic}')
        click.echo(f'    Fake: {fake}')
        for statement, real in facts.items():
            if real:
                click.echo(f'    True: {statement}')
        if r.explanation:
            click.echo(f'    Why:  {r.explanation}')
        if r.comment:
            click.echo(f'    Comment: {r.comment}')
        if r.source_url:
            click.echo(f'    Source: {r.source_url}')


@app.cli.command('errors')
@click.option('-n', '--limit', default=20, show_default=True, help='How many to show.')
@click.option('--full', is_flag=True, help='Also show where in the code each one happened.')
def errors_command(limit, full):
    """Show the newest unexpected errors."""
    print_errors(limit, full)


@app.cli.command('reset-pin')
@click.argument('username')
@click.option('--pin', help='New 4-digit PIN (random if omitted).')
def reset_pin_command(username, pin):
    """Set a new PIN for a player who forgot theirs, and print it."""
    user = User.query.filter(func.lower(User.username) == username.strip().lower()).first()
    if not user:
        raise click.ClickException(f'No player called "{username}".')
    if pin is None:
        pin = f'{secrets.randbelow(10000):04d}'
    elif not (len(pin) == 4 and pin.isdigit()):
        raise click.ClickException('The PIN must be 4 digits.')
    user.pin_hash = generate_password_hash(pin)
    db.session.commit()
    click.echo(f'New PIN for {user.username}: {pin}')


@app.cli.command('delete-player')
@click.argument('username')
@click.option('--yes', is_flag=True, help="Don't ask for confirmation.")
def delete_player_command(username, yes):
    """Delete a player and all their data."""
    user = User.query.filter(func.lower(User.username) == username.strip().lower()).first()
    if not user:
        raise click.ClickException(f'No player called "{username}".')
    if not yes:
        click.confirm(f'Delete {user.username} and all their scores, quizzes, uploads and badges?', abort=True)
    delete_player_data(user)
    click.echo(f'Deleted {username}.')


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


def render_setup():
    return render_template('setup.html', categories=CATEGORIES, lengths=GAME_LENGTH_OPTIONS,
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
                flash(_("Please upload a file or paste a URL"), "danger")
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
                           languages=LANGUAGE_OPTIONS, uploads=uploads, recommended=recommend_difficulty(user))


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
    """(topic, difficulty, rounds, language name) chosen on the setup page, or None after saying what's missing."""
    category_id = request.form.get('category')
    length_id = request.form.get('length')
    difficulty_id = request.form.get('difficulty')
    language_code = request.form.get('language', 'en')
    session['language'] = language_code

    if ((category_id not in CATEGORIES and category_id != SURPRISE) or length_id not in GAME_LENGTH_OPTIONS
            or difficulty_id not in DIFFICULTY_OPTIONS):
        flash(_("Please select a category, game length, and difficulty."), "warning")
        return None

    # "Surprise me" picks a category first, so each one is as likely however long its list is
    category_name = random.choice(list(TOPICS)) if category_id == SURPRISE else CATEGORIES[category_id]
    topic = random.choice(TOPICS[category_name])
    length_text = GAME_LENGTH_OPTIONS.get(length_id)
    questions_count = int(length_text.split("rounds")[0].split("(")[1].strip())
    return topic, DIFFICULTY_OPTIONS.get(difficulty_id), questions_count, LANGUAGE_OPTIONS.get(language_code, "English")


@app.route('/start_quiz', methods=['POST'])
def start_quiz():
    user = current_user()
    if not user:
        return redirect(url_for('index'))

    settings = quiz_settings_from_form()
    if not settings or not quiz_generation_allowed(user):
        return redirect(url_for('setup'))
    topic, difficulty_text, questions_count, language_name = settings

    try:
        # Fetch questions from AI
        time_limit = TIMED_ROUND_SECONDS if request.form.get('timed') else None
        create_quiz_session(user, topic, difficulty_text, questions_count, language_name, time_limit=time_limit)
        return redirect(url_for('quiz'))

    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('setup'))


# ---------- Live head-to-head (rules in live.py) --------------------------------------------------------------------
# Both players' pages ask /live/<code>/state every LIVE_POLL_MS; there are no websockets on Vercel's functions.
LIVE_POLL_MS = 1500


def live_state(row):
    """The match as live.py works on it."""
    return {'state': row.state, 'rounds': len(json.loads(row.questions_json)), 'current_round': row.current_round,
            'round_started_at': row.round_started_at, 'questions': json.loads(row.questions_json),
            'answers': json.loads(row.answers_json)}


def store_live(row, state, **extra):
    """Write the match back unless another request changed it since `row` was read. True if written."""
    written = db.session.execute(
        update(LiveMatch).where(LiveMatch.id == row.id, LiveMatch.version == row.version).values(
            state=state['state'], current_round=state['current_round'], round_started_at=state['round_started_at'],
            answers_json=json.dumps(state['answers']), version=row.version + 1, **extra)
    ).rowcount == 1
    db.session.commit()
    return written


def current_live(code, now):
    """(row, state) of the match moved on to `now`, or (None, None). Retries when another request wrote first."""
    for _attempt in range(3):
        row = LiveMatch.query.filter_by(code=code).first()
        if not row:
            return None, None
        db.session.refresh(row)
        state = live_state(row)
        if not live.advance(state, now, created_at=row.created_at) or store_live(row, state):
            db.session.refresh(row)
            return row, state
    return row, live_state(row)


def live_role(row, user):
    if user and user.id == row.host_id:
        return 'host'
    if user and user.id == row.guest_id:
        return 'guest'
    return None


def save_live_scores(row, state):
    """Once a match ends, add it to both players' games, like any finished quiz (history, stats, leaderboard,
    badges). Only the first request to get here does it."""
    claimed = db.session.execute(update(LiveMatch).where(LiveMatch.id == row.id, LiveMatch.scores_saved.is_(False))
                                 .values(scores_saved=True)).rowcount == 1
    db.session.commit()
    if not claimed:
        return
    rounds = state['rounds']
    for role, user_id in (('host', row.host_id), ('guest', row.guest_id)):
        entries = state['answers'][role]
        correct = sum(1 for e in entries if e and e['correct'])
        quiz = QuizSession(user_id=user_id, topic=row.topic, difficulty=row.difficulty,
                           questions_json=row.questions_json, explanations_json=row.explanations_json,
                           quotes_json=row.quotes_json, source_url=row.source_url,
                           answers_json=json.dumps([e['pick'] if e else None for e in entries]),
                           total_questions=rounds, current_question_index=rounds, score=correct, is_completed=True)
        db.session.add(quiz)
        db.session.flush()
        db.session.add(Score(user_id=user_id, topic=row.topic, difficulty=row.difficulty, score=correct,
                             total_questions=rounds, percentage=correct / rounds * 100, quiz_session_id=quiz.id))
    db.session.commit()
    for user_id in (row.host_id, row.guest_id):
        award_badges(db.session.get(User, user_id))


def live_payload(row, state, role, now):
    """What a player's page needs to show the match right now."""
    phase, seconds = live.phase(state, now)
    totals = live.visible_totals(state, now)
    names = {'host': db.session.get(User, row.host_id).username,
             'guest': db.session.get(User, row.guest_id).username if row.guest_id else None}
    index = state['current_round']
    payload = {'phase': phase, 'seconds': seconds, 'round': index + 1, 'rounds': state['rounds'], 'you': role,
               'players': {r: {'name': names[r], 'points': totals[r]['points'], 'correct': totals[r]['correct'],
                               'answered': bool(live.answer_of(state, r, index))} for r in live.ROLES}}
    explanations = json.loads(row.explanations_json or '[]')

    def round_result(i):
        facts = state['questions'][i]
        return {'fake': next(s for s, real in facts.items() if real is False),
                'explanation': explanations[i] if i < len(explanations) else None,
                'answers': {r: live.answer_of(state, r, i) for r in live.ROLES}}

    if phase in ('question', 'reveal'):
        # Same order for both players, different every round
        payload['options'] = random.Random(f'{row.code}:{index}').sample(list(state['questions'][index]), 4)
        mine = live.answer_of(state, role, index) if role else None
        payload['your_pick'] = mine['pick'] if mine else None
    if phase == 'reveal':
        payload['result'] = round_result(index)
    if phase == 'finished':
        payload['winner'] = live.winner(state)
        payload['summary'] = [round_result(i) for i in range(state['rounds'])]
    return payload


@app.route('/live/new', methods=['POST'])
def live_new():
    """Make a live match from the setup page's choices and show its invite link."""
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    settings = quiz_settings_from_form()
    if not settings or not quiz_generation_allowed(user):
        return redirect(url_for('setup'))
    topic, difficulty, rounds, language_name = settings
    try:
        quiz = get_ai_response(rounds, topic, difficulty, language_name)
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('setup'))
    row = LiveMatch(code=secrets.token_urlsafe(9), host_id=user.id, topic=topic[:100], difficulty=difficulty,
                    language=language_name, source=quiz.source, source_url=quiz.source_url,
                    questions_json=json.dumps(quiz.rounds), explanations_json=json.dumps(quiz.explanations),
                    quotes_json=json.dumps(quiz.quotes), state='waiting', current_round=0,
                    answers_json=json.dumps({'host': [], 'guest': []}), version=0, scores_saved=False,
                    created_at=utcnow())
    db.session.add(row)
    db.session.commit()
    return redirect(url_for('live_match', code=row.code))


@app.route('/live/<code>')
def live_match(code):
    row, state = current_live(code, utcnow())
    if not row:
        abort(404)
    user = current_user()
    if not user:
        # Back here after signing in
        session['next_url'] = url_for('live_match', code=code)
        flash(_("Sign in to play this live match."), "info")
        return redirect(url_for('index'))
    role = live_role(row, user)
    can_join = role is None and row.guest_id is None and state['state'] == 'waiting'
    if role is None and not can_join:
        flash(_("This match is already full."), "warning")
        return redirect(url_for('setup'))
    return render_template('live.html', match=row, role=role, can_join=can_join, poll_ms=LIVE_POLL_MS,
                           host_name=db.session.get(User, row.host_id).username,
                           invite_url=url_for('live_match', code=code, _external=True))


@app.route('/live/<code>/join', methods=['POST'])
def live_join(code):
    user = current_user()
    if not user:
        return redirect(url_for('live_match', code=code))
    now = utcnow()
    row, state = current_live(code, now)
    if not row:
        abort(404)
    if live_role(row, user) is None and state['state'] == 'waiting':
        live.start(state, now)
        # Only if nobody joined in the meantime
        db.session.execute(update(LiveMatch).where(
            LiveMatch.id == row.id, LiveMatch.guest_id.is_(None), LiveMatch.state == 'waiting',
            LiveMatch.host_id != user.id).values(
            guest_id=user.id, state=state['state'], current_round=0, round_started_at=state['round_started_at'],
            version=row.version + 1))
        db.session.commit()
    return redirect(url_for('live_match', code=code))


@app.route('/live/<code>/state')
def live_status(code):
    now = utcnow()
    row, state = current_live(code, now)
    if not row:
        return jsonify(error='not_found'), 404
    role = live_role(row, current_user())
    if role is None and not (row.guest_id is None and state['state'] == 'waiting'):
        return jsonify(error='forbidden'), 403
    if state['state'] == 'finished' and not row.scores_saved:
        save_live_scores(row, state)
    return jsonify(live_payload(row, state, role, now))


@app.route('/live/<code>/answer', methods=['POST'])
def live_answer(code):
    data = request.get_json(silent=True) or {}
    round_number = data.get('round')
    if not isinstance(round_number, int) or not isinstance(data.get('option'), str):
        return jsonify(ok=False), 400
    user = current_user()
    for _attempt in range(3):
        now = utcnow()
        row, state = current_live(code, now)
        role = live_role(row, user) if row else None
        if not role:
            return jsonify(ok=False), 403
        entry = live.record_answer(state, role, round_number - 1, data['option'], now)
        if not entry:
            return jsonify(ok=False)
        if store_live(row, state):
            return jsonify(ok=True)
    return jsonify(ok=False)


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
        # Timed mode: the browser submits when the countdown ends; the server also checks the clock, so a late
        # answer (or one sent with JavaScript turned off) can't beat the timer
        timed_out = bool(quiz_session.time_limit) and (
            request.form.get('timeout') == '1' or seconds_left(quiz_session) <= -TIMER_GRACE_SECONDS)
        if not selected_option and not timed_out:
            flash(_("Please select an option."), "warning")
        elif not claim_round(quiz_session, round_index):
            # Another request (a double click, or the timer firing during a click) already answered this round
            return redirect(url_for('quiz'))
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
                flash(_("Correct! You found the fake fact."), "success")
            elif timed_out:
                flash(_("Time's up! The fake fact was:") + " " + correct_answer, "danger")
            else:
                # Find the correct answer to show the user
                flash(_("Incorrect. The fake fact was:") + " " + correct_answer, "danger")

            quiz_session.round_started_at = None

            if quiz_session.current_question_index >= quiz_session.total_questions:
                quiz_session.is_completed = True

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

                flash_new_badges(db.session.get(User, quiz_session.user_id))
                return redirect(url_for('results'))

            db.session.commit()
            return redirect(url_for('quiz'))

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
                           topic=quiz_session.topic,
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
                           share=share_details(quiz_session),
                           challenge=challenge_comparison(quiz_session),
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
                           difficulty=DAILY_DIFFICULTY)


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
        save_quiz_session(user, challenge.topic, DAILY_DIFFICULTY, json.loads(challenge.questions_json),
                          json.loads(challenge.explanations_json), challenge.source,
                          daily_challenge_id=challenge.id,
                          quotes=json.loads(challenge.quotes_json) if challenge.quotes_json else None,
                          source_url=challenge.source_url)
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
    return render_template('history.html', scores=list(reversed(scores)), chart_points=chart_points)


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


@app.route('/chatbot')
def chatbot_page():
    """Display the chatbot interface."""
    return render_template('chatbot.html', suggestions=QUICK_HELP)


@app.route('/api/chatbot', methods=['POST'])
def chatbot_api():
    """API endpoint for chatbot messages."""
    try:
        data = request.get_json(silent=True) or {}
        user_message = str(data.get('message') or '').strip()

        if not user_message:
            return jsonify({'error': _("Please type a question.")}), 400
        if len(user_message) > MAX_CHAT_MESSAGE:
            return jsonify({'error': _("Please keep your message under {n} characters.").format(
                n=MAX_CHAT_MESSAGE)}), 400
        # Every message costs an AI call and is stored, so each address gets a limited number
        if over_limit('chat', client_ip()):
            return jsonify({'error': _("You're going a bit fast. Please wait a few minutes and try again.")}), 429

        # Each browser gets its own random conversation ID (Flask has no built-in session ID)
        if 'chat_id' not in session:
            session['chat_id'] = secrets.token_urlsafe(16)
        session_id = session['chat_id']
        chatbot_session = ChatbotSession.query.filter_by(session_id=session_id).first()

        if not chatbot_session:
            chatbot_session = ChatbotSession(session_id=session_id, conversation_json='[]')
            db.session.add(chatbot_session)
            db.session.commit()

        # Load conversation history from database
        conversation_history = json.loads(chatbot_session.conversation_json)

        # Get chatbot response
        language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
        response = get_chatbot_response(user_message, conversation_history, language_name)

        # Update conversation history
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": response.message})

        # Keep only last 20 messages to avoid database bloat
        if len(conversation_history) > 20:
            conversation_history = conversation_history[-20:]

        # Save to database
        chatbot_session.conversation_json = json.dumps(conversation_history)
        db.session.commit()

        return jsonify({
            'message': response.message,
            'suggested_action': response.suggested_action,
            # The link comes from the AI, so only pages on this site (never javascript: or other sites)
            'action_url': local_path(response.action_url),
        })

    except Exception as e:
        app.logger.error('Help chat failed: %r', e, exc_info=e)
        save_error(e)
        return jsonify({'error': _("Sorry, I encountered an error. Please try again.")}), 500


if __name__ == '__main__':
    # Development server. FLASK_DEBUG=1 turns on auto-reload and the in-browser debugger, which can run code,
    # so it's off unless asked for. For anything other people use, run serve.py instead.
    with app.app_context():
        init_database()
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=int(os.environ.get('PORT', 5003)))
