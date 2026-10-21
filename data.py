import wikipedia
from langchain_openai import OpenAIEmbeddings
from openai import OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv
import os
from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import Embeddings
import numpy as np
import re
import time

from default_text import MENU

# override=True so a debug-server reload picks up a changed .env
load_dotenv(override=True)

# Wikimedia throttles generic clients; identify the app per their API etiquette
wikipedia.set_user_agent("WikiFakeFact/1.0 (educational quiz game)")

    # class SimpleMockEmbeddings(Embeddings):
    #     def embed_documents(self, texts):
    #         return [self.embed_query(text) for text in texts]
    #
    #     def embed_query(self, text):
    #         np.random.seed(hash(text) % 2**32)
    #         return np.random.rand(1536).tolist()


OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")

# Models can be changed in .env without touching the code
QUIZ_MODEL = os.getenv("OPENAI_QUIZ_MODEL", "gpt-4o-mini")
ANALYSIS_MODEL = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-5-mini")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


# time.perf_counter()

class QuizQuestion(BaseModel):
    statement: str
    is_real: bool

class QuizSet(BaseModel):
    questions: list[QuizQuestion]
    explanation: str  # Why the fake fact is false, shown in the answer review

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


# Each knowledge-base article covers a whole category; rag_indexer.py tags every chunk with it as metadata "topic"
TOPIC_CATEGORY = {topic: category for category, topics in MENU.items() for topic in topics}
MAX_KNOWLEDGE_CHUNKS = 3


def select_relevant_chunks(topic, chunks):
    """Chunks that actually mention the quiz topic, best match first. "Mercury (planet)" matches "Mercury"."""
    name = re.sub(r'\s*\(.*\)$', '', topic).strip().lower()
    return [chunk for chunk in chunks if name and name in chunk.page_content.lower()][:MAX_KNOWLEDGE_CHUNKS]


def fetch_local_knowledge(query):
    """
    LangChain RAG Retrieval: Fetches content about a built-in topic from the Chroma vector store.

    Only chunks from the topic's own category article that mention the topic are used; otherwise nothing is
    returned, so the quiz falls back to the AI's own knowledge instead of an unrelated passage.
    """
    category = TOPIC_CATEGORY.get(query)
    if not category:
        return "", ""
    try:
        persist_directory = os.path.join(os.path.dirname(__file__), 'chroma_db')
        if os.path.exists(persist_directory):
            embeddings = OpenAIEmbeddings(
                model=EMBEDDING_MODEL,
                api_key=OPEN_AI_KEY
            )
            vectorstore = Chroma(persist_directory=persist_directory, embedding_function=embeddings)

            # Search more widely within the category, then keep the chunks that mention the topic
            candidates = vectorstore.similarity_search(query, k=10, filter={"topic": category})
            docs = select_relevant_chunks(query, candidates)
            if docs:
                combined_content = "\n\n".join([doc.page_content for doc in docs])
                return combined_content, "LangChain RAG"
        return "", ""
    except Exception as e:
        print(f"Error fetching local knowledge via LangChain: {e}")
        return "", ""


def fetch_wikipedia_article(query):
    try:
        # Only the article text is used, so skip preloading images/links/sections
        return wikipedia.page(title=query, auto_suggest=False).content, "Wikipedia"
    except Exception as e:
        print(f"Error fetching Wikipedia article: {e}")
        return "", ""


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

        # Fallback to LangChain RAG if Wikipedia fails
        if not article_content:
            print(f"Wikipedia fetch failed for '{article_title}'. Falling back to LangChain RAG...")
            article_content, source = fetch_local_knowledge(article_title)

        # Final fallback: If both fail, use AI's internal knowledge (Zero-shot RAG)
        if not article_content:
            print(f"Local RAG also failed for '{article_title}'. Using AI internal knowledge...")
            source = "AI Internal Knowledge"
            article_content = f"The topic is {article_title}. Use your internal knowledge to generate the quiz."

        print(f"Generating quiz using source: {source}")

    # Using Structured Outputs with beta.chat.completions.parse
    response = client.beta.chat.completions.parse(
        model=QUIZ_MODEL,
        messages=[
            {"role": "system",
             "content": f"You are a quiz generator for a {difficulty} audience. Adjust the complexity, vocabulary, and trickiness of the facts accordingly. The entire quiz MUST be in {language}."},
            {"role": "user", "content": (
                f"Analyze the following content: {article_content}\n\n"
                f"Task: Generate exactly {questions_count} quiz sets for a {difficulty} level in {language}. Each set must have 3 real facts and 1 fake fact.\n"
                f"For each set, set 'explanation' to one short sentence in {language} saying why the fake fact is false and what is actually true.\n"
                f"Also set 'topic' to a short English name (1-4 words, Title Case) for the main subject of the content, e.g. 'Photosynthesis' or 'French Revolution'.\n"
            )}
        ],
        response_format=QuizData,
    )

    # Extract the parsed data
    quiz_data_obj = response.choices[0].message.parsed

    # Convert the Pydantic object back to the format expected by the rest of the app
    formatted_quiz_data = []
    explanations = []
    for quiz_set in quiz_data_obj.quiz_sets:
        question_dict = {}
        for q in quiz_set.questions:
            question_dict[q.statement] = q.is_real
        formatted_quiz_data.append(question_dict)
        explanations.append(quiz_set.explanation)

    return formatted_quiz_data, source, quiz_data_obj.topic.strip(), explanations

 # google_response = google_client.models.generate_content(
    #     model="gemini-2.5-flash",  # Use a generative model, not embedding
    #     contents=[
    #         {
    #             "role": "user",
    #             "parts": [{"text": (
    #                 f"Analyze the following Wikipedia article: {article_content}\n\n"
    #                 f"Task: Generate exactly {questions_count} quiz sets for a {difficulty} level in {language}. "
    #                 f"Each set must have 3 real facts and 1 fake fact.\n"
    #                 f"{CONST_PROMPT_TEXT}"
    #             )}]
    #         }
    #     ],
    #     config=types.GenerateContentConfig(
    #         # This is the proper way to set the 'System' personality
    #         system_instruction=f"You are a quiz generator for a {difficulty} audience. Adjust complexity and vocabulary. The entire quiz MUST be in {language}.",
    #         temperature=0.7,
    #         response_mime_type="application/json",
    #         response_schema=QuizData,
    #     ),
    # )
    # print(google_response)
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
        # reasoning_effort="high",

    )
    print("analytics response")
    print(response)
    
    return response.choices[0].message.parsed
