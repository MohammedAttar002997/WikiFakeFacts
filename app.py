import os
import json
import random
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from models import db, User, Score, QuizSession
from data import get_ai_response, get_ai_analysis
from default_text import CATEGORIES, MENU, GAME_LENGTH_OPTIONS, DIFFICULTY_OPTIONS, LANGUAGE_OPTIONS
from wikipedia import DisambiguationError
from translations import TRANSLATIONS

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hackathon-secret-key')

# Database configuration
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'quiz.sqlite')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

@app.context_processor
def inject_translations():
    lang = session.get('language', 'en')
    return {'t': TRANSLATIONS.get(lang, TRANSLATIONS['en'])}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        username = request.form.get('username')
        if not username or len(username.strip()) <= 2:
            flash("Please enter a valid name (at least 3 characters).", "danger")
            return redirect(url_for('index'))
        
        session['username'] = username.strip()
        
        # Create user if not exists
        user = User.query.filter_by(username=session['username']).first()
        if not user:
            user = User(username=session['username'])
            db.session.add(user)
            db.session.commit()
        
        return render_template('setup.html', categories=CATEGORIES, lengths=GAME_LENGTH_OPTIONS, difficulties=DIFFICULTY_OPTIONS, languages=LANGUAGE_OPTIONS)
    
    if 'username' not in session:
        return redirect(url_for('index'))
    
    return render_template('setup.html', categories=CATEGORIES, lengths=GAME_LENGTH_OPTIONS, difficulties=DIFFICULTY_OPTIONS, languages=LANGUAGE_OPTIONS)

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
        flash("Please select a category, game length, and difficulty.", "warning")
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
        quiz_data = get_ai_response(questions_count, topic, difficulty_text, language_name)
        
        # Create a new quiz session
        quiz_session = QuizSession(
            user_id=user.id,
            topic=topic,
            difficulty=difficulty_text,
            questions_json=json.dumps(quiz_data),
            total_questions=questions_count,
            current_question_index=0,
            score=0
        )
        db.session.add(quiz_session)
        db.session.commit()
        
        session['quiz_session_id'] = quiz_session.id
        return redirect(url_for('quiz'))
        
    except DisambiguationError:
        flash("Wikipedia found multiple topics for this category. Trying again...", "info")
        return redirect(url_for('setup'))
    except Exception as e:
        flash(f"An error occurred: {str(e)}", "danger")
        return redirect(url_for('setup'))

@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    if 'quiz_session_id' not in session:
        return redirect(url_for('setup'))
    
    quiz_session = QuizSession.query.get(session['quiz_session_id'])
    if not quiz_session or quiz_session.is_completed:
        return redirect(url_for('setup'))
    
    questions = json.loads(quiz_session.questions_json)
    
    if request.method == 'POST':
        selected_option = request.form.get('option')
        if not selected_option:
            flash("Please select an option.", "warning")
        else:
            current_q = questions[quiz_session.current_question_index]
            # The options were shuffled in the template, we need to check the value
            is_correct = current_q.get(selected_option) is False # False means it's the fake one
            
            if is_correct:
                quiz_session.score += 1
                flash("Correct! You found the fake fact.", "success")
            else:
                # Find the correct answer to show the user
                correct_answer = next(k for k, v in current_q.items() if v is False)
                flash(f"Incorrect. The fake fact was: {correct_answer}", "danger")
            
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
                    percentage=percentage
                )
                db.session.add(final_score)
                db.session.commit()
                
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
                           options=options)

@app.route('/results')
def results():
    if 'quiz_session_id' not in session:
        return redirect(url_for('setup'))
    
    quiz_session = QuizSession.query.get(session['quiz_session_id'])
    if not quiz_session:
        return redirect(url_for('setup'))
    
    percentage = (quiz_session.score / quiz_session.total_questions) * 100
    
    return render_template('results.html', 
                           score=quiz_session.score, 
                           total=quiz_session.total_questions, 
                           percentage=round(percentage, 2),
                           topic=quiz_session.topic)

@app.route('/leaderboard')
def leaderboard():
    top_scores = Score.query.order_by(Score.percentage.desc()).limit(10).all()
    return render_template('leaderboard.html', scores=top_scores)

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
                'avg_score': round(avg_score, 2)
            })
    
    # Sort by games played or avg score
    players_data.sort(key=lambda x: x['games_played'], reverse=True)
    return render_template('players.html', players=players_data)

@app.route('/analysis')
@app.route('/analysis/<int:user_id>')
def analysis(user_id=None):
    if user_id:
        user = User.query.get_or_404(user_id)
    else:
        if 'username' not in session:
            return redirect(url_for('index'))
        user = User.query.filter_by(username=session['username']).first()
        if not user:
            return redirect(url_for('index'))
    
    user_scores = Score.query.filter_by(user_id=user.id).all()
    
    if not user_scores:
        flash("Play some games first to see your performance analysis!", "info")
        return redirect(url_for('setup'))
    
    # Prepare history for AI
    history = []
    topic_stats = {}
    for s in user_scores:
        history.append({
            'topic': s.topic,
            'score': s.score,
            'total': s.total_questions,
            'percentage': s.percentage,
            'difficulty': s.difficulty,
            'date': s.timestamp.strftime("%Y-%m-%d")
        })
        
        if s.topic not in topic_stats:
            topic_stats[s.topic] = {'total_score': 0, 'total_q': 0, 'count': 0}
        topic_stats[s.topic]['total_score'] += s.score
        topic_stats[s.topic]['total_q'] += s.total_questions
        topic_stats[s.topic]['count'] += 1
    
    # Basic stats for the table
    analysis_data = []
    for topic, stats in topic_stats.items():
        avg_percentage = (stats['total_score'] / stats['total_q']) * 100
        analysis_data.append({
            'topic': topic,
            'avg_percentage': round(avg_percentage, 2),
            'games_played': stats['count']
        })
    analysis_data.sort(key=lambda x: x['avg_percentage'], reverse=True)

    # Get AI Analysis
    try:
        lang = session.get('language', 'en')
        language_name = LANGUAGE_OPTIONS.get(lang, "English")
        ai_analysis = get_ai_analysis(json.dumps(history), language_name)
    except Exception as e:
        print(f"AI Analysis Error: {e}")
        ai_analysis = None

    return render_template('analysis.html', 
                           analysis_data=analysis_data, 
                           ai_analysis=ai_analysis,
                           profile_user=user)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5003)
