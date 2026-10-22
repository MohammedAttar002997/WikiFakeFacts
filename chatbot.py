"""
Navigation Chatbot Module
Provides AI-powered assistance to help users navigate the WikiFakeFact webapp.
"""

from openai import OpenAI
from pydantic import BaseModel
import os
from dotenv import load_dotenv

# override=True so a debug-server reload picks up a changed .env
load_dotenv(override=True)

OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")


# Pydantic model for structured chatbot responses
class ChatbotResponse(BaseModel):
    message: str
    suggested_action: str
    action_url: str


NAVIGATION_CONTEXT = """
You are a helpful navigation assistant for WikiFakeFact, an AI-powered quiz game. 
Your role is to help users navigate the application and answer questions about its features.

Here's what users can do in the app:
1. **Play Regular Quizzes**: Choose from categories like Sports, Animals, Countries, Fruits, Planets/Space, Technologies, and Musical Instruments
2. **Create Custom Quizzes**: Upload files (PDF, TXT, DOCX) or paste URLs to create quizzes from custom content
3. **View Leaderboard**: See top-performing players
4. **View Players Directory**: Browse all registered players and their stats
5. **Check My Progress**: View AI-powered analysis of quiz performance
6. **Adjust Difficulty**: Choose from various difficulty levels (Ages 7-99)
7. **Choose Language**: Play in English, Spanish, French, German, Italian, Portuguese, or Arabic

When users ask for help:
- Be friendly and encouraging
- Provide clear, concise guidance
- Suggest relevant features based on their interests
- Include a suggested action (e.g., "Start Quiz", "Upload File", "View Progress")
- Provide the corresponding URL for the suggested action

Available URLs:
- Home: /
- Setup/Start Quiz: /setup
- Custom Quiz: /custom_quiz
- Leaderboard: /leaderboard
- Players: /players
- My Progress: /analysis
- Quiz Page: /quiz
- Results: /results

Always be helpful and guide users to the right feature!
"""


def get_chatbot_response(user_message, conversation_history=None, language="English"):
    """
    Generate a chatbot response to help users navigate the app.

    Args:
        user_message (str): The user's question or request
        conversation_history (list): Previous messages in the conversation
        language (str): The player's interface language, for the reply

    Returns:
        ChatbotResponse: Structured response with message, suggested action, and URL
    """
    if not OPEN_AI_KEY:
        raise RuntimeError("OpenAI API key is required.")

    client = OpenAI(api_key=OPEN_AI_KEY)

    # Build conversation messages
    messages = [
        {"role": "system", "content": NAVIGATION_CONTEXT + (
            f"\nReply in {language}, the language of the player's screen, including the suggested action label, "
            "unless they write to you in another language.")}
    ]

    # Add conversation history if provided
    if conversation_history:
        messages.extend(conversation_history)

    # Add current user message
    messages.append({"role": "user", "content": user_message})

    # Use structured outputs for consistent response format. Errors are left to the caller, which tells the
    # player in their language
    response = client.beta.chat.completions.parse(
        model=CHAT_MODEL,
        messages=messages,
        response_format=ChatbotResponse,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ValueError('The assistant gave no usable reply')
    return parsed


# Common questions shown on the help page; their text is translated when shown (translations.dynamic_strings)
QUICK_HELP = [
    {
        "question": "How do I start playing?",
        "action": "Start Quiz",
        "url": "/setup"
    },
    {
        "question": "How do I create a custom quiz?",
        "action": "Create Custom Quiz",
        "url": "/custom_quiz"
    },
    {
        "question": "How can I see my progress?",
        "action": "View My Progress",
        "url": "/analysis"
    },
    {
        "question": "Who are the top players?",
        "action": "View Leaderboard",
        "url": "/leaderboard"
    },
    {
        "question": "Can I see other players?",
        "action": "Browse Players",
        "url": "/players"
    },
    {
        "question": "What languages are supported?",
        "action": "Start Quiz",
        "url": "/setup"
    },
    {
        "question": "What difficulty levels are available?",
        "action": "Start Quiz",
        "url": "/setup"
    }
]
