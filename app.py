import os
import json
import random
import secrets
import time
from datetime import datetime, timedelta

import click
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from sqlalchemy import func, inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError
from flask_migrate import Migrate, stamp, upgrade
from flask_wtf.csrf import CSRFError, CSRFProtect
from urllib.parse import urlparse
from alembic.migration import MigrationContext
from alembic.operations import Operations
from models import (db, utcnow, User, Score, QuizSession, CustomContent, ChatbotSession, PlayerAnalysis, DailyChallenge,
                    UserBadge)
from data import get_ai_response, get_ai_analysis, AIAnalysis
from default_text import CATEGORIES, MENU, GAME_LENGTH_OPTIONS, DIFFICULTY_OPTIONS, LANGUAGE_OPTIONS, LEADERBOARD_PERIODS
from wikipedia import DisambiguationError
from translations import (UI_LANGUAGES, format_date, template_context, template_globals, translate as _,
                          translate_plural as _n)
from content_extractor import extract_text_from_file, extract_text_from_url, validate_content_length
from chatbot import get_chatbot_response, get_quick_help_suggestions
from badges import BADGES, award_badges, compute_streaks, player_streaks


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hackathon-secret-key')

# File upload configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Database configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(basedir, 'quiz.sqlite'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Every POST must carry the session's CSRF token (forms: hidden csrf_token field; fetch: X-CSRFToken header).
# Tokens last as long as the session, so a quiz left open for a while can still be submitted.
app.config['WTF_CSRF_TIME_LIMIT'] = None
csrf = CSRFProtect(app)
# render_as_batch lets Alembic alter SQLite tables (it recreates them instead of using unsupported ALTERs)
migrate = Migrate(app, db, directory=os.path.join(basedir, 'migrations'), render_as_batch=True)

# Wrong-PIN throttling, keyed by lowercase username: {name: (failed_count, first_failure_time)}
MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 5 * 60
FAILED_PINS = {}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.context_processor
def inject_translations():
    return template_context()


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
    target = request.args.get('next', '/')
    # Only follow local paths, never another site
    if not target.startswith('/') or target.startswith('//'):
        target = '/'
    return redirect(target)


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
                        custom_title=None):
    """Generate a quiz, save it, and make it the player's current quiz."""
    quiz_data, source, ai_topic, explanations = get_ai_response(questions_count, topic, difficulty, language_name,
                                                                custom_content=custom_content)
    if custom_content:
        # Label custom quizzes by their subject so progress/leaderboard show a real topic:
        # the user's own title wins, then the AI-detected topic
        topic = (custom_title or '').strip() or ai_topic or "Custom Content"

    return save_quiz_session(user, topic, difficulty, quiz_data, explanations, source)


def save_quiz_session(user, topic, difficulty, quiz_data, explanations, source, daily_challenge_id=None):
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
        daily_challenge_id=daily_challenge_id
    )
    db.session.add(quiz_session)
    db.session.commit()

    session['quiz_session_id'] = quiz_session.id
    session['quiz_source'] = source
    return quiz_session



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


def current_user():
    """The logged-in player, or None."""
    if 'username' not in session:
        return None
    return User.query.filter_by(username=session['username']).first()


def build_answer_review(quiz_session):
    """Round-by-round answer review: which statement was fake, what the player picked, and why."""
    questions = json.loads(quiz_session.questions_json)
    answers = json.loads(quiz_session.answers_json or '[]')
    explanations = json.loads(quiz_session.explanations_json or '[]')
    return [{
        'facts': list(facts.items()),
        'picked': answers[i] if i < len(answers) else None,
        'correct': i < len(answers) and facts.get(answers[i]) is False,
        'explanation': explanations[i] if i < len(explanations) else None,
    } for i, facts in enumerate(questions[:quiz_session.current_question_index])]


def get_topic_stats(user_scores):
    """Per-topic accuracy for a player's scores, best topic first."""
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


def is_locked_out(key):
    attempts = FAILED_PINS.get(key)
    if not attempts:
        return False
    count, first_failure = attempts
    if time.time() - first_failure > PIN_LOCKOUT_SECONDS:
        FAILED_PINS.pop(key, None)
        return False
    return count >= MAX_PIN_ATTEMPTS


def record_failed_pin(key):
    count, first_failure = FAILED_PINS.get(key, (0, time.time()))
    FAILED_PINS[key] = (count + 1, first_failure)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('quiz_session_id', None)
    # The next player on this browser starts a fresh help conversation
    session.pop('chat_id', None)
    return redirect(url_for('index'))


def delete_player_data(user, chat_id=None):
    """Remove a player and everything stored about them (shared daily-challenge quizzes stay)."""
    for model in (UserBadge, PlayerAnalysis, Score, QuizSession, CustomContent):
        model.query.filter_by(user_id=user.id).delete()
    if chat_id:
        ChatbotSession.query.filter_by(session_id=chat_id).delete()
    db.session.delete(user)
    db.session.commit()


def check_account_pin(user, pin):
    """Verify a PIN for an account action, counting wrong guesses toward the sign-in lockout."""
    key = user.username.lower()
    if is_locked_out(key):
        flash(_("Too many wrong PINs for this name. Please wait a few minutes and try again."), "danger")
        return False
    if not user.pin_hash or not check_password_hash(user.pin_hash, pin):
        record_failed_pin(key)
        flash(_("That PIN isn't right."), "danger")
        return False
    FAILED_PINS.pop(key, None)
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
    return render_template('account.html', user=user, counts=counts)


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

        key = username.lower()
        if is_locked_out(key):
            flash(_("Too many wrong PINs for this name. Please wait a few minutes and try again."), "danger")
            return redirect(url_for('index'))

        # Names are matched case-insensitively so "phoenix" can't impersonate "Phoenix"
        user = User.query.filter(func.lower(User.username) == key).first()
        if not user:
            user = User(username=username, pin_hash=generate_password_hash(pin))
            db.session.add(user)
            db.session.commit()
        elif not user.pin_hash:
            # Player from before PINs existed: the first PIN entered claims the name
            user.pin_hash = generate_password_hash(pin)
            db.session.commit()
        elif not check_password_hash(user.pin_hash, pin):
            record_failed_pin(key)
            flash(_('The name “{name}” is already taken. Enter its PIN, or choose a different name.').format(
                name=user.username), "danger")
            return redirect(url_for('index'))

        FAILED_PINS.pop(key, None)
        session['username'] = user.username

        return render_template('setup.html', categories=CATEGORIES, lengths=GAME_LENGTH_OPTIONS, difficulties=DIFFICULTY_OPTIONS, languages=LANGUAGE_OPTIONS,
                           daily_topic=daily_topic(today_utc()))
    
    if 'username' not in session:
        return redirect(url_for('index'))
    
    return render_template('setup.html', categories=CATEGORIES, lengths=GAME_LENGTH_OPTIONS, difficulties=DIFFICULTY_OPTIONS, languages=LANGUAGE_OPTIONS,
                           daily_topic=daily_topic(today_utc()))


@app.route('/custom_quiz', methods=['GET', 'POST'])
def custom_quiz():
    if 'username' not in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        try:
            user = User.query.filter_by(username=session['username']).first()

            # Ensure user exists in database
            if not user:
                user = User(username=session['username'])
                db.session.add(user)
                db.session.commit()

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

                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)

                try:
                    content = extract_text_from_file(filepath)
                    source_info = filename
                    content_type = 'file'
                    os.remove(filepath)  # Clean up after extraction
                except Exception as e:
                    flash(_("Error extracting file content: {error}").format(error=e), "danger")
                    return redirect(url_for('custom_quiz'))

            elif request.form.get('url'):
                url = request.form.get('url').strip()

                if not url:
                    flash(_("Please provide a valid URL"), "danger")
                    return redirect(url_for('custom_quiz'))

                try:
                    content = extract_text_from_url(url)
                    source_info = url
                    content_type = 'url'
                except Exception as e:
                    flash(_("Error extracting webpage content: {error}").format(error=e), "danger")
                    return redirect(url_for('custom_quiz'))

            else:
                flash(_("Please upload a file or paste a URL"), "danger")
                return redirect(url_for('custom_quiz'))

            # Validate content length
            try:
                validate_content_length(content)
            except ValueError as e:
                flash(_("Content validation failed: {error}").format(error=e), "danger")
                return redirect(url_for('custom_quiz'))

            # Store custom content in database, reusing the saved copy when the same content comes in again
            custom_content_obj = saved_content or CustomContent.query.filter_by(
                user_id=user.id, source=source_info, content=content).first()
            if not custom_content_obj:
                custom_content_obj = CustomContent(
                    user_id=user.id,
                    title=request.form.get('title', 'Untitled'),
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
                custom_title = request.form.get('title') or (saved_content.title if saved_content else None)
                quiz_session = create_quiz_session(user, "Custom Content", difficulty_text, questions_count,
                                                   language_name, custom_content=content,
                                                   custom_title=custom_title)

                # Save the content under the quiz topic so "Practice my weak spots" can reuse it
                custom_content_obj.title = quiz_session.topic
                db.session.commit()
                return redirect(url_for('quiz'))

            except Exception as e:
                flash(_("Error generating quiz: {error}").format(error=e), "danger")
                return redirect(url_for('custom_quiz'))

        except Exception as e:
            flash(_("An unexpected error occurred: {error}").format(error=e), "danger")
            return redirect(url_for('custom_quiz'))

    user = current_user()
    uploads = (CustomContent.query.filter_by(user_id=user.id).order_by(CustomContent.created_at.desc()).all()
               if user else [])
    return render_template('custom_quiz.html', lengths=GAME_LENGTH_OPTIONS, difficulties=DIFFICULTY_OPTIONS,
                           languages=LANGUAGE_OPTIONS, uploads=uploads)


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
    if 'username' not in session:
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
    
    user = User.query.filter_by(username=session['username']).first()
    
    try:
        # Fetch questions from AI
        language_name = LANGUAGE_OPTIONS.get(language_code, "English")
        create_quiz_session(user, topic, difficulty_text, questions_count, language_name)
        return redirect(url_for('quiz'))
        
    except DisambiguationError:
        flash(_("Wikipedia found multiple topics for this category. Trying again..."), "info")
        return redirect(url_for('setup'))
    except Exception as e:
        flash(_("An error occurred: {error}").format(error=e), "danger")
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
        if not selected_option:
            flash(_("Please select an option."), "warning")
        else:
            current_q = questions[quiz_session.current_question_index]
            # The options were shuffled in the template, we need to check the value
            is_correct = current_q.get(selected_option) is False # False means it's the fake one

            # Remember the pick for the answer review on the results page
            answers = json.loads(quiz_session.answers_json or '[]')
            answers.append(selected_option)
            quiz_session.answers_json = json.dumps(answers)

            if is_correct:
                quiz_session.score += 1
                flash(_("Correct! You found the fake fact."), "success")
            else:
                # Find the correct answer to show the user
                correct_answer = next(k for k, v in current_q.items() if v is False)
                flash(_("Incorrect. The fake fact was:") + " " + correct_answer, "danger")
            
            quiz_session.current_question_index += 1
            
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

    # Prepare current question
    current_q = questions[quiz_session.current_question_index]
    options = list(current_q.keys())
    random.shuffle(options)
    
    return render_template('quiz.html', 
                           question_num=quiz_session.current_question_index + 1,
                           total_questions=quiz_session.total_questions,
                           topic=quiz_session.topic,
                           options=options,
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
                           review=build_answer_review(quiz_session))


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
        flash(_("An error occurred: {error}").format(error=e), "danger")
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
                           review=build_answer_review(quiz_session))


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
                'id': user.id,
                'username': user.username,
                'games_played': len(scores),
                'avg_score': round(avg_score, 2),
                'streak': compute_streaks([s.timestamp.date() for s in scores if s.timestamp])[0],
                'badges': UserBadge.query.filter_by(user_id=user.id).count(),
            })
    
    # Sort by games played or avg score
    players_data.sort(key=lambda x: x['games_played'], reverse=True)
    return render_template('players.html', players=players_data)


@app.route('/analysis')
@app.route('/analysis/<int:user_id>')
def analysis(user_id=None):
    if 'username' not in session:
        return redirect(url_for('index'))
    user = User.query.filter_by(username=session['username']).first()
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
            print(f"AI Analysis Error: {e}")
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
    if 'username' not in session:
        return redirect(url_for('index'))
    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('index'))

    user_scores = Score.query.filter_by(user_id=user.id).order_by(Score.id).all()
    practice_topic, custom_content = find_practice_topic(user, get_topic_stats(user_scores))
    if not practice_topic:
        flash(_("Play a few quizzes first so we can find a topic to practice."), "info")
        return redirect(url_for('setup'))

    language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
    questions_count = 5  # Medium length
    try:
        if custom_content:
            create_quiz_session(user, "Custom Content", practice_topic['difficulty'], questions_count, language_name,
                                custom_content=custom_content.content, custom_title=practice_topic['topic'])
        else:
            create_quiz_session(user, practice_topic['topic'], practice_topic['difficulty'], questions_count,
                                language_name)
        return redirect(url_for('quiz'))
    except Exception as e:
        flash(_("An error occurred: {error}").format(error=e), "danger")
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
        data = request.get_json()
        user_message = data.get('message', '').strip()

        if not user_message:
            return jsonify({'error': 'Message cannot be empty'}), 400

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
        print(f"Chatbot API Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        init_database()
    app.run(debug=True, port=5003)
