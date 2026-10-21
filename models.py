from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    pin_hash = db.Column(db.String(255), nullable=True)  # Null for players created before PINs existed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    scores = db.relationship('Score', backref='user', lazy=True)

class Score(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(100), nullable=False)
    difficulty = db.Column(db.String(50), nullable=False, default='Medium')
    score = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    percentage = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
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

class CustomContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    content_type = db.Column(db.String(20), nullable=False)  # 'file' or 'url'
    source = db.Column(db.String(500), nullable=True)  # filename or URL
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='custom_contents')

class PlayerAnalysis(db.Model):
    """Last AI analysis per player, reused until they finish another quiz or switch language."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    language = db.Column(db.String(50), nullable=False)
    last_score_id = db.Column(db.Integer, nullable=False)  # Newest score included in the analysis
    analysis_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ChatbotSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), unique=True, nullable=False)
    conversation_json = db.Column(db.Text, nullable=False, default='[]')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)