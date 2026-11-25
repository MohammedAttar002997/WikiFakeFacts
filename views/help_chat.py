"""Finn, the help chat (rules and prompt in chatbot.py, the feature guide in help_guide.py): the /chatbot page and the
/api/chatbot endpoint it talks to. Routes register on the app when app.py imports this.

The AI call goes through app.get_chatbot_response, so tests can replace it there.
"""
import json
import secrets

from flask import jsonify, render_template, request, session
from sqlalchemy import func

import app as core
import chatbot as rules
import help_guide
import leagues
from app import (EVENT_BEST_OF, MAX_CHAT_MESSAGE, MAX_FILE_MB, TIMED_ROUND_SECONDS, app, client_ip, current_user,
                 daily_attempt, daily_topic, db, event_week, local_path, over_limit, save_error)
from badges import player_streaks
from default_text import LANGUAGE_OPTIONS
from models import ChatbotSession, Score
from translations import translate as _

KEEP_MESSAGES = 20   # Of a conversation, newest first; older ones are dropped
RECENT_GAMES = 3     # Latest quizzes Finn is told about
WEAK_MIN_GAMES = 2   # Games on a topic before it can count as weak
WEAK_TOPICS = 3


def guide_entries():
    return help_guide.features(TIMED_ROUND_SECONDS, EVENT_BEST_OF, MAX_FILE_MB)


def weakest_topics(rows, min_games=WEAK_MIN_GAMES, size=WEAK_TOPICS):
    """Topics with at least `min_games` games and an average under 70%, weakest first. rows: [(topic, percentage)]."""
    totals = {}
    for topic, percentage in rows:
        if topic and percentage is not None:
            totals.setdefault(topic, []).append(percentage)
    averages = [(sum(p) / len(p), topic) for topic, p in totals.items() if len(p) >= min_games]
    return [topic for average, topic in sorted(averages) if average < 70][:size]


def player_summary(user):
    """What Finn is told about a signed-in player (see chatbot.describe_player)."""
    from views.leagues import entry_for, group_standings  # Imported late: views.leagues imports app too
    streak, best = player_streaks(user)
    recent = (Score.query.filter_by(user_id=user.id).order_by(Score.timestamp.desc()).limit(RECENT_GAMES).all())
    week = event_week(core.utcnow().date())
    entry = entry_for(user, week)
    league = None
    if entry:
        table = group_standings(week, entry.tier, entry.group_no)
        mine = next((row for row in table if row['user'] and row['user'].id == user.id), None)
        if mine:
            league = {'tier': leagues.TIERS[entry.tier], 'rank': mine['rank'], 'size': len(table), 'points': mine['points']}
    today = core.today_utc()
    return {
        'name': user.username, 'streak': streak, 'best_streak': best,
        'games': db.session.query(func.count(Score.id)).filter(Score.user_id == user.id).scalar(),
        'recent': [(s.topic, s.score, s.total_questions) for s in recent],
        'weakest': weakest_topics(db.session.query(Score.topic, Score.percentage).filter(Score.user_id == user.id)),
        'league': league, 'daily_done': daily_attempt(user, today) is not None, 'daily_topic': daily_topic(today),
    }


def chat_owner():
    """Whose chat this browser may show: the signed-in player's id, or 'guest'."""
    user = current_user()
    return user.id if user else 'guest'


def conversation():
    """This browser's chat with the current player (a ChatbotSession), or None before its first message. A chat
    started by someone else on this browser (another player, or a guest before signing in) is never shown."""
    chat_id = session.get('chat_id')
    if not chat_id or session.get('chat_owner') != chat_owner():
        return None
    return ChatbotSession.query.filter_by(session_id=chat_id).first()


@app.route('/chatbot')
def chatbot_page():
    """The chat, with this browser's conversation so far."""
    chat = conversation()
    history = json.loads(chat.conversation_json) if chat else []
    return render_template('chatbot.html', suggestions=rules.QUICK_HELP, history=history)


@app.route('/api/chatbot', methods=['POST'])
def chatbot_api():
    """One message to Finn: {message} in, {message, actions: [{label, url}]} out."""
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

        # Each browser and player gets its own random conversation ID (Flask has no built-in session ID)
        chat = conversation()
        if not chat and (session.get('chat_owner') != chat_owner() or 'chat_id' not in session):
            session['chat_id'], session['chat_owner'] = secrets.token_urlsafe(16), chat_owner()
        if not chat:
            chat = ChatbotSession(session_id=session['chat_id'], conversation_json='[]')
            db.session.add(chat)
            db.session.commit()
        history = json.loads(chat.conversation_json)

        user = current_user()
        entries = guide_entries()
        language_name = LANGUAGE_OPTIONS.get(session.get('language', 'en'), "English")
        response = core.get_chatbot_response(user_message, history, language_name,
                                             guide=help_guide.guide_text(entries),
                                             player_text=rules.describe_player(player_summary(user) if user else None))

        history += [{"role": "user", "content": user_message}, {"role": "assistant", "content": response.message}]
        chat.conversation_json = json.dumps(history[-KEEP_MESSAGES:])
        db.session.commit()

        # Buttons only to pages of the guide, and only on this site (never javascript: or other sites)
        actions = [a for a in rules.clean_actions(response.actions, help_guide.known_urls(entries))
                   if local_path(a['url'])]
        return jsonify({'message': response.message, 'actions': actions})

    except Exception as e:
        app.logger.error('Help chat failed: %r', e, exc_info=e)
        save_error(e)
        return jsonify({'error': _("Sorry, I encountered an error. Please try again.")}), 500
