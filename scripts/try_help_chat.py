"""Ask Finn, the help chat, a few questions with your own OpenAI key and print his answers and buttons.

    python scripts/try_help_chat.py                       # built-in questions, as a guest and as a sample player
    python scripts/try_help_chat.py "How do I join a class?"   # one question of your own

Each question is one call to the chat model. Nothing is saved, and no database is needed: the player is made up.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # chatbot.py loads .env from here

import chatbot  # noqa: E402
import help_guide  # noqa: E402

# Same numbers as app.py's TIMED_ROUND_SECONDS, EVENT_BEST_OF and MAX_FILE_MB (not imported: app.py needs a database)
ENTRIES = help_guide.features(timed_seconds=20, event_best_of=3, max_file_mb=4)
PLAYER = {'name': 'Sam', 'streak': 4, 'best_streak': 9, 'games': 37, 'recent': [('Saturn', 2, 5), ('Cat', 5, 5)],
          'weakest': ['Saturn', 'Cricket'], 'league': {'tier': 'Silver', 'rank': 6, 'size': 18, 'points': 210},
          'daily_done': False, 'daily_topic': 'Saxophone'}
QUESTIONS = [('guest', 'How do I start playing?'), ('player', 'What should I play next?'),
             ('player', 'How do leagues work?'), ('guest', 'Can I play with my class at school?'),
             ('player', 'My PDF upload says there is no text. Why?'), ('player', 'Which one is fake: the Sun is a star?'),
             ('guest', "What's the weather tomorrow?")]


def ask(who, question):
    reply = chatbot.get_chatbot_response(question, [], 'English', guide=help_guide.guide_text(ENTRIES),
                                         player_text=chatbot.describe_player(PLAYER if who == 'player' else None))
    buttons = chatbot.clean_actions(reply.actions, help_guide.known_urls(ENTRIES))
    print(f'\n[{who}] {question}\nFinn: {reply.message}')
    print('Buttons: ' + (', '.join(f"{b['label']} ({b['url']})" for b in buttons) or 'none'))


if __name__ == '__main__':
    for who, question in ([('player', ' '.join(sys.argv[1:]))] if len(sys.argv) > 1 else QUESTIONS):
        ask(who, question)
