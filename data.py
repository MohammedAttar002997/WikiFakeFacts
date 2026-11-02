import json
import os
from functools import cache
from typing import NamedTuple
from urllib.parse import quote

import numpy as np
import wikipedia
from langchain_openai import OpenAIEmbeddings
from openai import OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv

from models import db, KnowledgeChunk

# override=True so a debug-server reload picks up a changed .env
load_dotenv(override=True)

# Wikimedia throttles generic clients; identify the app per their API etiquette
wikipedia.set_user_agent("WikiFakeFact/1.0 (educational quiz game)")

OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")

# Models can be changed in .env without touching the code
QUIZ_MODEL = os.getenv("OPENAI_QUIZ_MODEL", "gpt-4o-mini")
ANALYSIS_MODEL = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-5-mini")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


class QuizQuestion(BaseModel):
    statement: str
    is_real: bool

class QuizSet(BaseModel):
    questions: list[QuizQuestion]
    explanation: str  # Why the fake fact is false, shown in the answer review
    source_quote: str  # Sentence of the content that shows the fake fact is false, word for word ('' if none)

class QuizData(BaseModel):
    topic: str  # Short name for the content's subject, used to label custom quizzes
    quiz_sets: list[QuizSet]

class TopicInsight(BaseModel):
    topic: str
    strength: str
    weakness: str
    recommendation: str

class AIAnalysis(BaseModel):
    overall_summary: str
    strongest_area: str
    weakest_area: str
    learning_path: list[str]
    topic_insights: list[TopicInsight]


KB_PATH = os.path.join(os.path.dirname(__file__), 'knowledge_base.json')
RAG_CHUNKS = 8  # Passages given to the AI: enough true facts for a long game (11 rounds of 3)


@cache
def load_knowledge_base():
    """{topic: saved Wikipedia article} for every built-in topic (made by build_knowledge_base.py)."""
    with open(KB_PATH, encoding='utf-8') as f:
        return json.load(f)


def search_knowledge_chunks(topic):
    """RAG retrieval: the passages of the topic's saved article closest to it by embedding, in article order.
    Empty when rag_indexer.py hasn't indexed the topic with the current embedding model."""
    chunks = KnowledgeChunk.query.filter_by(topic=topic, model=EMBEDDING_MODEL).order_by(KnowledgeChunk.id).all()
    if not chunks:
        return ""
    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=OPEN_AI_KEY)
    query = np.asarray(embeddings.embed_query(f"Key facts about {topic}"), dtype=np.float32)
    vectors = np.stack([np.frombuffer(chunk.embedding, dtype=np.float32) for chunk in chunks])
    similarity = vectors @ query / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(query))
    best = sorted(np.argsort(-similarity)[:RAG_CHUNKS])
    return "\n\n".join(chunks[i].content for i in best)


def fetch_local_knowledge(topic):
    """Content about a built-in topic for when Wikipedia is down: the best passages of its saved article, or the
    whole saved article if the search isn't available. ("", "") for other topics."""
    try:
        content = search_knowledge_chunks(topic)
        if content:
            return content, "Knowledge base (RAG)"
    except Exception as e:
        db.session.rollback()  # A failed query mustn't break the rest of the request
        print(f"Knowledge-base search failed for '{topic}': {e!r}")
    article = load_knowledge_base().get(topic, "")
    return (article, "Knowledge base") if article else ("", "")


def fetch_wikipedia_article(query):
    try:
        # Only the article text is used, so skip preloading images/links/sections
        return wikipedia.page(title=query, auto_suggest=False).content, "Wikipedia"
    except Exception as e:
        print(f"Error fetching Wikipedia article: {e}")
        return "", ""


QUIZ_ATTEMPTS = 2
MIN_QUOTE_LENGTH = 20  # Shorter "quotes" are fragments that would match almost any article
WIKIPEDIA_SOURCES = {"Wikipedia", "Knowledge base (RAG)", "Knowledge base"}  # The saved articles are Wikipedia's too


class GeneratedQuiz(NamedTuple):
    rounds: list  # {statement: is_real} per round
    source: str  # Where the content came from, e.g. "Wikipedia"
    topic: str  # The AI's short name for the content's subject
    explanations: list  # Why each round's fake fact is false
    quotes: list  # Sentence of the content that shows it, or None when the AI's quote isn't really in the content
    source_url: str | None  # The Wikipedia article the content came from


def wikipedia_url(title):
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))


def normalize_text(text):
    return " ".join(text.split()).casefold()


def verified_quote(quote_text, normalized_content):
    """The AI's quote if it really is in the content (ignoring spacing and case), else None. Models sometimes
    paraphrase or invent a "quote", and a made-up source is worse than none."""
    quote_text = quote_text.strip().strip('"“”«»\'')
    # Models often end a copied clause with a full stop where the article has a comma or semicolon; without its
    # last punctuation mark it's still word for word
    for candidate in (quote_text, quote_text.rstrip('.,;:!?')):
        if len(candidate) >= MIN_QUOTE_LENGTH and normalize_text(candidate) in normalized_content:
            return candidate
    return None


def playable_rounds(quiz_sets):
    """(statements, explanation, quote) for each round the game can play: four different, non-empty statements
    with exactly one of them fake."""
    rounds = []
    for quiz_set in quiz_sets:
        statements = {q.statement.strip(): q.is_real for q in quiz_set.questions if q.statement.strip()}
        if len(statements) == 4 and list(statements.values()).count(False) == 1:
            rounds.append((statements, quiz_set.explanation, quiz_set.source_quote))
    return rounds


def get_ai_response(questions_count, article_title, difficulty="Medium", language="English", custom_content=None):
    if not OPEN_AI_KEY:
        raise RuntimeError("OpenAI API key is required.")

    client = OpenAI(api_key=OPEN_AI_KEY)

    # If custom content is provided, use it directly
    if custom_content:
        article_content = custom_content
        source = "Custom Content"
        print(f"Generating quiz using source: {source}")
    else:
        # Try Wikipedia first
        article_content, source = fetch_wikipedia_article(article_title)

        # Fall back to the saved copy of the article if Wikipedia fails
        if not article_content:
            print(f"Wikipedia fetch failed for '{article_title}'. Falling back to the knowledge base...")
            article_content, source = fetch_local_knowledge(article_title)

        # Final fallback: If both fail, use AI's internal knowledge (Zero-shot RAG)
        if not article_content:
            print(f"No saved article for '{article_title}' either. Using AI internal knowledge...")
            source = "AI Internal Knowledge"
            article_content = f"The topic is {article_title}. Use your internal knowledge to generate the quiz."

        print(f"Generating quiz using source: {source}")

    # One spare round, so a round that comes back unplayable (or missing) doesn't cost the whole quiz
    sets_requested = questions_count + 1
    messages = [
        {"role": "system",
         "content": f"You are a quiz generator for a {difficulty} audience. Adjust the complexity, vocabulary, and trickiness of the facts accordingly. The entire quiz MUST be in {language}."},
        {"role": "user", "content": (
            f"Analyze the following content: {article_content}\n\n"
            f"Task: Generate exactly {sets_requested} quiz sets for a {difficulty} level in {language}. Each set must have 3 real facts and 1 fake fact.\n"
            f"For each set, set 'explanation' to one short sentence in {language} saying why the fake fact is false and what is actually true.\n"
            f"Base each set's single fake fact on one specific sentence of the content with one detail changed (the 3 real facts stay true), and set 'source_quote' to that original sentence, copied exactly as it is written in the content: same words, same language, not translated or shortened. Leave 'source_quote' empty only if the content has no text to quote.\n"
            f"Also set 'topic' to a short English name (1-4 words, Title Case) for the main subject of the content, e.g. 'Photosynthesis' or 'French Revolution'.\n"
        )}
    ]
    # The model doesn't always follow the format (a round with two fakes, a repeated statement, a set too few, a
    # refusal), so only playable rounds are kept, with one more try if too few are left
    for attempt in range(QUIZ_ATTEMPTS):
        # Using Structured Outputs with beta.chat.completions.parse
        response = client.beta.chat.completions.parse(model=QUIZ_MODEL, messages=messages, response_format=QuizData)
        quiz_data_obj = response.choices[0].message.parsed
        rounds = playable_rounds(quiz_data_obj.quiz_sets if quiz_data_obj else [])
        if len(rounds) >= questions_count:
            break
        print(f"AI returned {len(rounds)} playable rounds of {questions_count} (attempt {attempt + 1})")
    else:
        raise ValueError(f"The AI returned only {len(rounds)} playable rounds of {questions_count}")

    rounds = rounds[:questions_count]
    # The model's own knowledge has no text to quote from
    normalized_content = normalize_text(article_content) if source != "AI Internal Knowledge" else ""
    return GeneratedQuiz(
        rounds=[statements for statements, _, _ in rounds],
        source=source,
        topic=quiz_data_obj.topic.strip(),
        explanations=[explanation for _, explanation, _ in rounds],
        quotes=[verified_quote(quote_text, normalized_content) if normalized_content else None
                for _, _, quote_text in rounds],
        source_url=wikipedia_url(article_title) if source in WIKIPEDIA_SOURCES else None,
    )


def get_ai_analysis(user_history_json, language="English"):
    if not OPEN_AI_KEY:
        raise RuntimeError("OpenAI API key is required.")
    
    client = OpenAI(api_key=OPEN_AI_KEY)

    response = client.beta.chat.completions.parse(
        model=ANALYSIS_MODEL,
        messages=[
            {"role": "system", "content": f"You are an expert educational analyst. Analyze the user's quiz history and provide deep insights and a learning path. The entire analysis MUST be in {language}."},
            {"role": "user", "content": f"Here is the user's quiz history in JSON format: {user_history_json}"}
        ],
        response_format=AIAnalysis,
    )
    
    return response.choices[0].message.parsed
