from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


def utcnow():
    """The current UTC time without tzinfo, as stored in the database (the stdlib utcnow() is deprecated)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    pin_hash = db.Column(db.String(255), nullable=True)  # Null for players created before PINs existed
    created_at = db.Column(db.DateTime, default=utcnow)
    favorite_topic = db.Column(db.String(100), nullable=True)  # One of CATEGORIES' names, shown on the profile
    # Profile photo as a 256×256 WebP; deferred so looking up a player doesn't load the image
    avatar = db.deferred(db.Column(db.LargeBinary, nullable=True))
    avatar_updated_at = db.Column(db.DateTime, nullable=True)  # Set while there's a photo; versions its URL
    scores = db.relationship('Score', backref='user', lazy=True)

class Score(db.Model):
    # One score per quiz, however many times its last answer is sent
    __table_args__ = (db.UniqueConstraint('quiz_session_id', name='uq_score_quiz_session_id'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    difficulty = db.Column(db.String(50), nullable=False, default='Medium')
    score = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    percentage = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=utcnow)
    quiz_session_id = db.Column(db.Integer, db.ForeignKey('quiz_session.id'), nullable=True)  # For the answer review

class QuizSession(db.Model):
    # One attempt per player at each daily challenge (rows outside the daily challenge have no challenge id)
    __table_args__ = (db.UniqueConstraint('user_id', 'daily_challenge_id', name='uq_quiz_session_user_daily'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    difficulty = db.Column(db.String(50), nullable=False, default='Medium')
    questions_json = db.Column(db.Text, nullable=False) # Store the generated questions as JSON
    explanations_json = db.Column(db.Text, nullable=True)  # Why each round's fake fact is false
    answers_json = db.Column(db.Text, nullable=True)  # Statement the player picked in each round
    current_question_index = db.Column(db.Integer, default=0)
    score = db.Column(db.Integer, default=0)
    total_questions = db.Column(db.Integer, nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    daily_challenge_id = db.Column(db.Integer, db.ForeignKey('daily_challenge.id'), nullable=True)
    time_limit = db.Column(db.Integer, nullable=True)  # Seconds per round in timed mode, None otherwise
    round_started_at = db.Column(db.DateTime, nullable=True)  # When the current round was first shown (timed mode)
    share_code = db.Column(db.String(16), unique=True, nullable=True)  # Unguessable id for the public result link
    challenge_of_id = db.Column(db.Integer, db.ForeignKey('quiz_session.id'), nullable=True)  # Quiz this one replays

class DailyChallenge(db.Model):
    """One shared quiz per day and language; every player gets the same questions."""
    __table_args__ = (db.UniqueConstraint('date', 'language'),)
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)  # UTC date, YYYY-MM-DD
    language = db.Column(db.String(50), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    questions_json = db.Column(db.Text, nullable=False)
    explanations_json = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow)

class CustomContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    content_type = db.Column(db.String(20), nullable=False)  # 'file' or 'url'
    source = db.Column(db.String(500), nullable=True)  # filename or URL
    created_at = db.Column(db.DateTime, default=utcnow)
    user = db.relationship('User', backref='custom_contents')

class PlayerAnalysis(db.Model):
    """Last AI analysis per player, reused until they finish another quiz or switch language."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    language = db.Column(db.String(50), nullable=False)
    last_score_id = db.Column(db.Integer, nullable=False)  # Newest score included in the analysis
    analysis_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

class UserBadge(db.Model):
    """A badge a player has earned (see badges.py for the definitions)."""
    __table_args__ = (db.UniqueConstraint('user_id', 'badge'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    badge = db.Column(db.String(50), nullable=False)
    earned_at = db.Column(db.DateTime, default=utcnow)

class PinAttempt(db.Model):
    """Wrong-PIN guesses per (lowercase) name, so the sign-in lockout survives app restarts."""
    name = db.Column(db.String(80), primary_key=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    first_failure = db.Column(db.DateTime, nullable=False, default=utcnow)

class ChatbotSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), unique=True, nullable=False)
    conversation_json = db.Column(db.Text, nullable=False, default='[]')
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
class RateLimit(db.Model):
    """Requests per key (like 'chat:203.0.113.9') in the current window, shared by every server instance."""
    key = db.Column(db.String(120), primary_key=True)
    hits = db.Column(db.Integer, nullable=False, default=0)
    window_start = db.Column(db.DateTime, nullable=False, default=utcnow)

class ErrorLog(db.Model):
    """Unexpected errors, kept because Vercel's own logs only last minutes. Only the newest few hundred are kept,
    and nothing about the player: just the page, the error and where in the code it happened."""
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    method = db.Column(db.String(10), nullable=False)
    path = db.Column(db.String(200), nullable=False)
    error = db.Column(db.Text, nullable=False)
    traceback = db.Column(db.Text, nullable=False)

class KnowledgeChunk(db.Model):
    """A passage of a topic's saved article (knowledge_base.json) with its embedding, for the fallback search when
    Wikipedia is down. Filled by rag_indexer.py."""
    id = db.Column(db.Integer, primary_key=True)  # Article order within a topic
    topic = db.Column(db.String(100), nullable=False, index=True)
    model = db.Column(db.String(100), nullable=False)  # Searches only use chunks embedded with the current model
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(db.LargeBinary, nullable=False)  # float32 vector
