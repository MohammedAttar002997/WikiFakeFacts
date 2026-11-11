import json
import math
import os
import re
from collections import Counter
from functools import cache
from typing import Literal, NamedTuple
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
CHECK_MODEL = os.getenv("OPENAI_CHECK_MODEL", QUIZ_MODEL)
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

Verdict = Literal['supported', 'contradicted', 'not_in_text']

class RoundCheck(BaseModel):
    verdicts: list[Verdict]  # One per statement, in the order they were given

class QuizCheck(BaseModel):
    rounds: list[RoundCheck]

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


SHORT_CONTENT = 8000  # Characters; shorter content is checked whole
COMMON_IN = 0.3  # A word in more than this share of the paragraphs (like "Germany" in its own article) tells nothing
PASSAGES_PER_STATEMENT = 3
STOP_WORDS = frozenset('about after also among been before being between both could during each from have into '
                       'like many more most other over same since some such than that their them then there these '
                       'they this those through under until were what when where which while with would'.split())


def keywords(text):
    """The words of `text` that can tie a statement to a paragraph: numbers of two or more digits (without
    thousands separators, so "100,000" matches "100000"), and words of five or more letters."""
    text = re.sub(r'(?<=\d)[,.  ](?=\d{3}\b)', '', text)
    numbers = {n for n in re.findall(r'\d+(?:\.\d+)?', text) if len(n.replace('.', '')) >= 2}
    words = {w.casefold() for w in re.findall(r'[^\W\d_]{5,}', text)} - STOP_WORDS
    return numbers, words


def relevant_passages(content, rounds):
    """The paragraphs of `content` that the fact check needs: for each statement, the PASSAGES_PER_STATEMENT
    paragraphs that share the most with it, where rare words weigh more than common ones and numbers double.
    Numbers and names are what the quiz model changes to make a fake, so the paragraph that backs or refutes a
    statement shares them. The whole content when it's short, or when a round matches nothing at all (statements
    in another language than the content, say), so no round is checked against too little."""
    if len(content) <= SHORT_CONTENT:
        return content
    paragraphs = [p for p in content.split('\n') if p.strip()]
    found = [keywords(p) for p in paragraphs]
    counts = Counter(k for numbers, words in found for k in numbers | words)
    weight = {k: math.log(len(paragraphs) / n) for k, n in counts.items() if n <= COMMON_IN * len(paragraphs)}
    keep = set()
    for statements, _, _ in rounds:
        matched = False
        for statement in statements:
            numbers, words = keywords(statement)
            scores = [(2 * sum(weight.get(k, 0) for k in numbers & p_numbers)
                       + sum(weight.get(k, 0) for k in words & p_words), i)
                      for i, (p_numbers, p_words) in enumerate(found)]
            best = [i for score, i in sorted(scores, reverse=True)[:PASSAGES_PER_STATEMENT] if score > 0]
            keep.update(best)
            matched = matched or bool(best)
        if not matched:
            return content
    return '\n'.join(paragraphs[i] for i in sorted(keep))


def check_rounds(content, rounds):
    """Ask the AI, statement by statement, whether the content supports each one (only the paragraphs that
    relate to the statements are sent, which keeps the check fast on long articles). Returns a list of verdicts per
    round (same order as the statements), or None when the check couldn't run: then the rounds are played as they
    are, so an API hiccup never costs the player the quiz. The checker isn't told which statement is the fake."""
    listing = "\n\n".join(
        f"Round {i + 1}:\n" + "\n".join(f"{j + 1}. {statement}" for j, statement in enumerate(statements))
        for i, (statements, _, _) in enumerate(rounds))
    try:
        client = OpenAI(api_key=OPEN_AI_KEY)
        response = client.beta.chat.completions.parse(model=CHECK_MODEL, response_format=QuizCheck, messages=[
            {"role": "system", "content": (
                "You check quiz statements against a source text, using only that text. For each statement answer "
                "'supported' if the text says it (a paraphrase or translation counts), 'contradicted' if the text says "
                "something different (another number, date, name, place or relation), or 'not_in_text' if the text "
                "doesn't say either way. A number the text gives elsewhere for the same thing counts as supported.")},
            {"role": "user", "content": (
                f"Source text:\n{relevant_passages(content, rounds)}\n\n"
                f"Statements:\n{listing}\n\n"
                f"Return exactly {len(rounds)} rounds, each with one verdict per statement, in the order given.")},
        ])
        parsed = response.choices[0].message.parsed
        verdicts = [r.verdicts for r in parsed.rounds]
    except Exception as e:
        print(f"Fact check failed, playing the rounds unchecked: {e!r}")
        return None
    if len(verdicts) != len(rounds) or any(len(v) != len(s) for v, (s, _, _) in zip(verdicts, rounds)):
        print("Fact check came back in the wrong shape, playing the rounds unchecked")
        return None
    return verdicts


def round_quality(statements, verdicts):
    """2: the fake isn't backed by the content and every real fact is. 1: nothing is wrong, but a real fact
    isn't in the content (or the round wasn't checked). 0: wrong, because the content backs the fake or
    contradicts a real fact, so a player who knows the topic would be marked wrong."""
    if verdicts is None:
        return 1
    real = [v for is_real, v in zip(statements.values(), verdicts) if is_real]
    fake = [v for is_real, v in zip(statements.values(), verdicts) if not is_real]
    if 'supported' in fake or 'contradicted' in real:
        return 0
    return 2 if all(v == 'supported' for v in real) else 1


def choose_rounds(scored, needed):
    """The `needed` best (quality, round) pairs, in the order they were made; wrong rounds (quality 0) only
    when there's nothing else, so a quiz is never lost to the check."""
    best = sorted(range(len(scored)), key=lambda i: -scored[i][0])[:needed]
    return [scored[i][1] for i in sorted(best)]


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
    # refusal), so only playable rounds are kept. It also gets facts wrong: a "fake" the article actually backs, or a
    # "real" fact it contradicts. So rounds made from real content are checked against it, and wrong ones are only
    # played if there's nothing else. One more try if too few good rounds are left; rounds of both tries count.
    grounded = source != "AI Internal Knowledge"
    scored, quiz_data_obj = [], None
    for attempt in range(QUIZ_ATTEMPTS):
        # Using Structured Outputs with beta.chat.completions.parse
        response = client.beta.chat.completions.parse(model=QUIZ_MODEL, messages=messages, response_format=QuizData)
        parsed = response.choices[0].message.parsed
        quiz_data_obj = parsed or quiz_data_obj
        rounds = playable_rounds(parsed.quiz_sets if parsed else [])
        verdicts = check_rounds(article_content, rounds) if grounded and rounds else None
        scored += [(round_quality(r[0], verdicts[i] if verdicts else None), r) for i, r in enumerate(rounds)]
        good = sum(1 for quality, _ in scored if quality > 0)
        if good >= questions_count:
            break
        print(f"AI returned {good} good rounds of {questions_count} (attempt {attempt + 1})")
    else:
        if len(scored) < questions_count:
            raise ValueError(f"The AI returned only {len(scored)} playable rounds of {questions_count}")
        print("Not enough rounds passed the fact check; playing some that didn't")

    rounds = choose_rounds(scored, questions_count)
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
