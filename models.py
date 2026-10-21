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
    scores = db.relationship('Score', backref='user', lazy=True)

class Score(db.Model):
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