"""
Finn, the help chat: answers questions about WikiFakeFact from the feature guide (help_guide.py) and, for a signed-in
player, from a short summary of their own games (describe_player), so "what should I play next?" gets a real answer.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

# override=True so a debug-server reload picks up a changed .env
load_dotenv(override=True)

OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
MAX_ACTIONS = 2


class Action(BaseModel):
    label: str = Field(description="Short button text, 1 to 4 words, in the reply's language")
    url: str = Field(description="A page from the guide, for example /daily")


class ChatbotResponse(BaseModel):
    message: str
    actions: list[Action] = Field(description=f"0 to {MAX_ACTIONS} buttons to the pages the answer is about")


RULES = f"""
You are Finn, a friendly detective fox and the guide of WikiFakeFact, a free trivia game for every age: each round
shows facts from Wikipedia and the player spots the one that is fake. If asked who you are, say you're Finn.

How you answer:
- Use only the guide below for facts about the game. If something isn't in it, say you don't know rather than guess,
  and never invent features, pages, prices or rules.
- Keep it short: two to four sentences, plain words, warm but not childish. Players range from children to retirees.
- Add 0 to {MAX_ACTIONS} buttons, only to pages from the guide that help with the question (none for small talk).
- If you know about the player (below), use it to personalize, for example suggest their weak topics or today's
  challenge if they haven't played it. Never make up numbers about them.
- Never say which statement is fake in a quiz, and don't help answer a quiz in progress: that's the game.
- For things unrelated to WikiFakeFact, say kindly that you can only help with the game.

The guide (feature (page): what it is):
"""


def describe_player(summary):
    """A few lines about the signed-in player for Finn, or '' for guests. summary: {name, streak, best_streak, games,
    recent: [(topic, score, total)], weakest: [topic], league: {tier, rank, size, points} or None, daily_done,
    daily_topic}."""
    if not summary:
        return ''
    lines = [f"Name: {summary['name']}. Quizzes finished: {summary['games']}. "
             f"Streak: {summary['streak']} days in a row (best {summary['best_streak']})."]
    if summary['recent']:
        lines.append('Latest quizzes: ' + '; '.join(f'{topic} {score}/{total}' for topic, score, total in summary['recent']))
    if summary['weakest']:
        lines.append('Weakest topics so far: ' + ', '.join(summary['weakest']))
    league = summary['league']
    if league:
        lines.append(f"This week's league: {league['tier']}, place {league['rank']} of {league['size']}, "
                     f"{league['points']} points.")
    else:
        lines.append("Not in this week's league yet (finishing any quiz joins it).")
    lines.append(f"Today's daily challenge ({summary['daily_topic']}): "
                 + ('already played.' if summary['daily_done'] else 'not played yet.'))
    return '\n'.join(lines)


def system_prompt(guide, language, player_text=''):
    prompt = RULES + guide + (f"\n\nReply in {language}, the language of the player's screen, button labels "
                              "included, unless they write to you in another language.")
    if player_text:
        prompt += "\n\nAbout the player you're talking to:\n" + player_text
    else:
        prompt += "\n\nThe player isn't signed in. Signing in (name and PIN) is needed for most features."
    return prompt


def clean_actions(actions, allowed):
    """At most MAX_ACTIONS buttons, each to a page of the guide (the AI's links are never trusted as they are)."""
    kept = []
    for action in actions or []:
        url = (getattr(action, 'url', '') or '').strip()
        label = (getattr(action, 'label', '') or '').strip()
        if url.split('?')[0].split('#')[0] in allowed and label and all(url != a['url'] for a in kept):
            kept.append({'label': label[:40], 'url': url})
    return kept[:MAX_ACTIONS]


def get_chatbot_response(user_message, conversation_history=None, language="English", guide='', player_text=''):
    """Finn's reply (a ChatbotResponse) to the player's message, given the conversation so far."""
    if not OPEN_AI_KEY:
        raise RuntimeError("OpenAI API key is required.")

    client = OpenAI(api_key=OPEN_AI_KEY)
    messages = [{"role": "system", "content": system_prompt(guide, language, player_text)}]
    messages.extend(conversation_history or [])
    messages.append({"role": "user", "content": user_message})

    # Structured output for a dependable shape. Errors are left to the caller, which tells the player in their language
    response = client.beta.chat.completions.parse(model=CHAT_MODEL, messages=messages, response_format=ChatbotResponse)
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ValueError('The assistant gave no usable reply')
    return parsed


# Common questions shown on the help page; their text is translated when shown (translations.dynamic_strings)
QUICK_HELP = [
    {"question": "How do I start playing?", "url": "/setup"},
    {"question": "What should I play next?", "url": "/setup"},
    {"question": "How does the daily challenge work?", "url": "/daily"},
    {"question": "How do leagues work?", "url": "/league"},
    {"question": "How can I play with friends?", "url": "/challenges"},
    {"question": "How do I create a custom quiz?", "url": "/custom_quiz"},
    {"question": "How can I see my progress?", "url": "/analysis"},
]
