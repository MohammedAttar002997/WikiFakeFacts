import os
import json
import random
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from models import db, User, Score, QuizSession, CustomContent, ChatbotSession
from data import get_ai_response, get_ai_analysis
from default_text import CATEGORIES, MENU, GAME_LENGTH_OPTIONS, DIFFICULTY_OPTIONS, LANGUAGE_OPTIONS
from wikipedia import DisambiguationError
from translations import TRANSLATIONS
from content_extractor import extract_text_from_file, extract_text_from_url, validate_content_length
from chatbot import get_chatbot_response, get_quick_help_suggestions


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
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'quiz.sqlite')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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

            # Check if file upload or URL paste
            if 'file' in request.files and request.files['file'].filename:
                file = request.files['file']

                if not allowed_file(file.filename):
                    flash("Invalid file type. Allowed types: PDF, TXT, DOCX", "danger")
                    return redirect(url_for('custom_quiz'))

                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)

                try:
                    content = extract_text_from_file(filepath)
                    source_info = filename
                    os.remove(filepath)  # Clean up after extraction
                except Exception as e:
                    flash(f"Error extracting file content: {str(e)}", "danger")
                    return redirect(url_for('custom_quiz'))

            elif request.form.get('url'):
                url = request.form.get('url').strip()

                if not url:
                    flash("Please provide a valid URL", "danger")
                    return redirect(url_for('custom_quiz'))

                try:
                    content = extract_text_from_url(url)
                    source_info = url
                except Exception as e:
                    flash(f"Error extracting webpage content: {str(e)}", "danger")
                    return redirect(url_for('custom_quiz'))

            else:
                flash("Please upload a file or paste a URL", "danger")
                return redirect(url_for('custom_quiz'))

            # Validate content length
            try:
                validate_content_length(content)
            except ValueError as e:
                flash(f"Content validation failed: {str(e)}", "danger")
                return redirect(url_for('custom_quiz'))

            # Store custom content in database
            custom_content_obj = CustomContent(
                user_id=user.id,
                title=request.form.get('title', 'Untitled'),
                content=content,
                content_type='file' if 'file' in request.files else 'url',
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
                flash("Please select game length and difficulty.", "warning")
                return redirect(url_for('custom_quiz'))

            length_text = GAME_LENGTH_OPTIONS.get(length_id)
            questions_count = int(length_text.split("rounds")[0].split("(")[1].strip())
            difficulty_text = DIFFICULTY_OPTIONS.get(difficulty_id)

            try:
                # Generate quiz from custom content
                language_name = LANGUAGE_OPTIONS.get(language_code, "English")
                quiz_data, source = get_ai_response(questions_count, "Custom Content", difficulty_text, language_name,
                                                    custom_content=content)
                session['quiz_source'] = source

                # Create quiz session
                quiz_session = QuizSession(
                    user_id=user.id,
                    topic="Custom Content",
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

            except Exception as e:
                flash(f"Error generating quiz: {str(e)}", "danger")
                return redirect(url_for('custom_quiz'))

        except Exception as e:
            flash(f"An unexpected error occurred: {str(e)}", "danger")
            return redirect(url_for('custom_quiz'))

    return render_template('custom_quiz.html', lengths=GAME_LENGTH_OPTIONS, difficulties=DIFFICULTY_OPTIONS,
                           languages=LANGUAGE_OPTIONS)


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
        quiz_data, source = get_ai_response(questions_count, topic, difficulty_text, language_name)
        session['quiz_source'] = source

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
                           options=options,
                           source=session.get('quiz_source', 'Wikipedia'))


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

        # Get or create chatbot session in database using Flask session ID
        session_id = session.get('_id', 'default')
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
        db.create_all()
    app.run(debug=True, port=5003)
