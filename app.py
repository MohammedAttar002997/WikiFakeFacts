import hmac
import io
import os
import json
import random
import secrets
from datetime import datetime, timedelta

import click
from flask import Flask, abort, g, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import case, func, inspect, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError
from flask_migrate import Migrate, stamp, upgrade
from flask_wtf.csrf import CSRFError, CSRFProtect
from urllib.parse import urlparse
from alembic.migration import MigrationContext
from alembic.operations import Operations
from models import (db, utcnow, User, Score, QuizSession, CustomContent, ChatbotSession, PlayerAnalysis, DailyChallenge,
                    UserBadge, PinAttempt, RateLimit)
from data import get_ai_response, get_ai_analysis, AIAnalysis
from default_text import CATEGORIES, MENU, GAME_LENGTH_OPTIONS, DIFFICULTY_OPTIONS, LANGUAGE_OPTIONS, LEADERBOARD_PERIODS
from wikipedia import DisambiguationError
from translations import (UI_LANGUAGES, format_date, template_context, template_globals, translate as _,
                          translate_plural as _n)
from content_extractor import (ContentError, extract_text_from_upload, extract_text_from_url,
                               validate_content_length)
from chatbot import get_chatbot_response, get_quick_help_suggestions
from badges import BADGES, award_badges, compute_streaks, player_streaks


app = Flask(__name__)
DEV_SECRET_KEY = 'hackathon-secret-key'  # Only for local development; serve.py refuses to run with it
app.secret_key = os.environ.get('SECRET_KEY') or DEV_SECRET_KEY
ON_VERCEL = bool(os.environ.get('VERCEL'))  # Vercel sets VERCEL=1 for builds and functions
if ON_VERCEL and app.secret_key == DEV_SECRET_KEY:
    raise RuntimeError('SECRET_KEY is not set. Add a long random value in the Vercel project settings.')
# Browsers don't send the session cookie on cross-site POSTs (a second layer next to CSRF tokens)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Set SESSION_COOKIE_SECURE=1 when the site is served over HTTPS, so the cookie is never sent unencrypted
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE') == '1'

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
# Requests per key and window, counted in the database so every server instance shares them. They cap what one
# visitor can cost: AI calls (chat, quizzes) and rows (accounts, chats).
RATE_LIMITS = {
    'chat': (20, timedelta(minutes=10)),     # help-chat messages per address
    'quiz': (20, timedelta(hours=1)),        # AI-generated quizzes per player
    'quiz_ip': (40, timedelta(hours=1)),     # … and per address, across accounts
    'signup': (10, timedelta(hours=1)),      # new accounts per address
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
    return {'me': current_user()}


app.jinja_env.globals.update(template_globals())
app.add_template_filter(format_date, 'date')
app.add_template_filter(lambda value: format_date(value, with_time=True), 'datetime')


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
                        custom_title=None, time_limit=None):
    """Generate a quiz, save it, and make it the player's current quiz."""
    quiz_data, source, ai_topic, explanations = get_ai_response(questions_count, topic, difficulty, language_name,
                                                                custom_content=custom_content)
    if custom_content:
        # Label custom quizzes by their subject so progress/leaderboard show a real topic:
        # the user's own title wins, then the AI-detected topic
        topic = (custom_title or '').strip() or ai_topic or "Custom Content"

    return save_quiz_session(user, topic, difficulty, quiz_data, explanations, source, time_limit=time_limit)


def save_quiz_session(user, topic, difficulty, quiz_data, explanations, source, daily_challenge_id=None,
                      time_limit=None, challenge_of_id=None):
    """Save already-generated questions as the player's current quiz."""
    quiz_session = QuizSession(
        user_id=user.id,
        topic=topic[:100],
        difficulty=difficulty,
        questions_json=json.dumps(quiz_data),
        explanations_json=json.dumps(explanations),
        answers_json='[]',
        total_questions=len(quiz_data),
        current_question_index=0,
        score=0,
        daily_challenge_id=daily_challenge_id,
        time_limit=time_limit,
        challenge_of_id=challenge_of_id
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
    quiz_data, source, _ai_topic, explanations = get_ai_response(DAILY_ROUNDS, topic, DAILY_DIFFICULTY, language_name)
    challenge = DailyChallenge(date=date, language=language_name, topic=topic, source=source,
                               questions_json=json.dumps(quiz_data), explanations_json=json.dumps(explanations))
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


def build_answer_review(quiz_session):
    """Round-by-round answer review: which statement was fake, what the player picked, and why."""
    questions = json.loads(quiz_session.questions_json)
    answers = json.loads(quiz_session.answers_json or '[]')
    explanations = json.loads(quiz_session.explanations_json or '[]')
    return [{
        'facts': list(facts.items()),
        'picked': answers[i] if i < len(answers) else None,
        'correct': i < len(answers) and facts.get(answers[i]) is False,
        'timed_out': i < len(answers) and answers[i] is None,
        'explanation': explanations[i] if i < len(explanations) else None,
    } for i, facts in enumerate(questions[:quiz_session.current_question_index])]


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
    menu_topics = {topic for topics in MENU.values() for topic in topics}
    for data in reversed(analysis_data):
        if data['topic'] in menu_topics:
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


def pin_attempt_allowed(key):
    """Count a PIN attempt for `key` and say whether it may be checked: at most MAX_PIN_ATTEMPTS per PIN_LOCKOUT
    window. The attempt is counted before the (deliberately slow) PIN check, so guesses sent in parallel can't all
    get past the limit; a correct PIN clears the count."""
    return count_in_window(PinAttempt, key, PIN_LOCKOUT, 'failures', 'first_failure') <= MAX_PIN_ATTEMPTS


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
    db.session.rollback()
    app.logger.error('%s failed: %r', request.path, error, exc_info=error)
    flash(message, "danger")


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
    db.session.commit()


@app.route('/')
def index():
    return render_template('index.html')


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
    for model in (UserBadge, PlayerAnalysis, Score, QuizSession, CustomContent):
        model.query.filter_by(user_id=user.id).delete()
    if chat_id:
        ChatbotSession.query.filter_by(session_id=chat_id).delete()
    db.session.delete(user)
    db.session.commit()


def check_account_pin(user, pin):
    """Verify a PIN for an account action, counting wrong guesses toward the sign-in lockout."""
    key = user.username.lower()
    if not pin_attempt_allowed(key):
        flash(_("Too many wrong PINs for this name. Please wait a few minutes and try again."), "danger")
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
                           daily_topic=daily_topic(today_utc()), timed_seconds=TIMED_ROUND_SECONDS,
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
        if not pin_attempt_allowed(key):
            flash(_("Too many wrong PINs for this name. Please wait a few minutes and try again."), "danger")
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

            if not length_id or not difficulty_id:
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


@app.route('/start_quiz', methods=['POST'])
def start_quiz():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    
    category_id = request.form.get('category')
    length_id = request.form.get('length')
    difficulty_id = request.form.get('difficulty')
    language_code = request.form.get('language', 'en')
    session['language'] = language_code
    
    if not category_id or not length_id or not difficulty_id:
        flash(_("Please select a category, game length, and difficulty."), "warning")
        return redirect(url_for('setup'))
    
    category_name = CATEGORIES.get(category_id)
    topics = MENU.get(category_name)
    topic = random.choice(topics)
    
    length_text = GAME_LENGTH_OPTIONS.get(length_id)
    questions_count = int(length_text.split("rounds")[0].split("(")[1].strip())
    
    difficulty_text = DIFFICULTY_OPTIONS.get(difficulty_id)
    if not quiz_generation_allowed(user):
        return redirect(url_for('setup'))

    try:
        # Fetch questions from AI
        language_name = LANGUAGE_OPTIONS.get(language_code, "English")
        time_limit = TIMED_ROUND_SECONDS if request.form.get('timed') else None
        create_quiz_session(user, topic, difficulty_text, questions_count, language_name, time_limit=time_limit)
        return redirect(url_for('quiz'))
        
    except DisambiguationError:
        flash(_("Wikipedia found multiple topics for this category. Trying again..."), "info")
        return redirect(url_for('setup'))
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('setup'))


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
        current_q = questions[quiz_session.current_question_index]
        # Timed mode: the browser submits when the countdown ends; the server also checks the clock, so a late
        # answer (or one sent with JavaScript turned off) can't beat the timer
        timed_out = bool(quiz_session.time_limit) and (
            request.form.get('timeout') == '1' or seconds_left(quiz_session) <= -TIMER_GRACE_SECONDS)
        if not selected_option and not timed_out:
            flash(_("Please select an option."), "warning")
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

            quiz_session.current_question_index += 1
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
    
    percentage = (quiz_session.score / quiz_session.total_questions) * 100

    return render_template('results.html',
                           score=quiz_session.score,
                           total=quiz_session.total_questions,
                           percentage=round(percentage, 2),
                           topic=quiz_session.topic,
                           is_daily=quiz_session.daily_challenge_id is not None,
                           share=share_details(quiz_session),
                           challenge=challenge_comparison(quiz_session),
                           review=build_answer_review(quiz_session))


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
                      time_limit=original.time_limit, challenge_of_id=original.id)
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
                          daily_challenge_id=challenge.id)
        return redirect(url_for('quiz'))
    except Exception as e:
        report_failure(_("We couldn't create your quiz right now. Please try again in a moment."), e)
        return redirect(url_for('daily'))


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
    return render_template('review.html', quiz_session=quiz_session, score=score,
                           share=share_details(quiz_session), review=build_answer_review(quiz_session))


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
        conditions.append(Score.topic.notin_([t for topics in MENU.values() for t in topics]))
    elif category != 'all':
        conditions.append(Score.topic.in_(MENU[CATEGORIES[category]]))
    if difficulty != 'all':
        conditions.append(Score.difficulty == DIFFICULTY_OPTIONS[difficulty])

    # One row per player: total correct answers across all their quizzes, ties broken by accuracy
    total_points = func.sum(Score.score)
    total_questions = func.sum(Score.total_questions)
    rows = (db.session.query(User, total_points, total_questions, func.count(Score.id))
            .join(Score, Score.user_id == User.id)
            .filter(*conditions)
            .group_by(User.id)
            .order_by(total_points.desc(), (total_points * 1.0 / total_questions).desc())
            .limit(10)
            .all())
    standings = [{
        'user': user,
        'points': points,
        'total_questions': questions,
        'quizzes': quizzes,
        'percentage': points / questions * 100 if questions else 0,
    } for user, points, questions, quizzes in rows]
    filters = {'period': period, 'category': category, 'difficulty': difficulty}
    return render_template('leaderboard.html', standings=standings, filters=filters,
                           filtered=any(v != 'all' for v in filters.values()),
                           periods=LEADERBOARD_PERIODS, categories=CATEGORIES, difficulties=DIFFICULTY_OPTIONS)


@app.route('/players')
def players():
    all_users = User.query.all()
    players_data = []
    for user in all_users:
        scores = Score.query.filter_by(user_id=user.id).all()
        if scores:
            avg_score = sum(s.percentage for s in scores) / len(scores)
            players_data.append({
                'user': user,
                'username': user.username,
                'games_played': len(scores),
                'avg_score': round(avg_score, 2),
                'streak': compute_streaks([s.timestamp.date() for s in scores if s.timestamp])[0],
                'badges': UserBadge.query.filter_by(user_id=user.id).count(),
            })
    
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
    if saved and saved.last_score_id == latest_score_id and saved.language == language_name:
        ai_analysis = AIAnalysis.model_validate_json(saved.analysis_json)
    else:
        history = [{
            'topic': s.topic,
            'score': s.score,
            'total': s.total_questions,
            'percentage': s.percentage,
            'difficulty': s.difficulty,
            'date': s.timestamp.strftime("%Y-%m-%d")
        } for s in user_scores]
        try:
            ai_analysis = get_ai_analysis(json.dumps(history), language_name)
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
            # Fall back to the last saved analysis, even if it's missing the newest quizzes
            ai_analysis = AIAnalysis.model_validate_json(saved.analysis_json) if saved else None

    # Players who finished quizzes before badges existed get theirs on their next visit
    flash_new_badges(user)
    earned = {b.badge: b.earned_at for b in UserBadge.query.filter_by(user_id=user.id)}
    current_streak, best_streak = player_streaks(user)

    return render_template('analysis.html',
                           analysis_data=analysis_data,
                           ai_analysis=ai_analysis,
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
    suggestions = get_quick_help_suggestions()
    return render_template('chatbot.html', suggestions=suggestions)


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
        response = get_chatbot_response(user_message, conversation_history)

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
            'action_url': response.action_url
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error('Help chat failed: %r', e, exc_info=e)
        return jsonify({'error': _("Sorry, I encountered an error. Please try again.")}), 500


if __name__ == '__main__':
    # Development server. FLASK_DEBUG=1 turns on auto-reload and the in-browser debugger, which can run code,
    # so it's off unless asked for. For anything other people use, run serve.py instead.
    with app.app_context():
        init_database()
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=int(os.environ.get('PORT', 5003)))
