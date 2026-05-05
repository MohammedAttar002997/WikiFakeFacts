import wikipedia
from langchain_openai import OpenAIEmbeddings
from openai import OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv
import json
import os
import random
from color_format import COLOR_FORMAT_MAP
from default_text import CONST_PROMPT_TEXT, show_error_message
from google import genai
from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import Embeddings
from google.genai import types
import numpy as np

load_dotenv()

class SimpleMockEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        np.random.seed(hash(text) % 2**32)
        return np.random.rand(1536).tolist()


OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")

# Define Pydantic models for Structured Outputs
class QuizQuestion(BaseModel):
    statement: str
    is_real: bool

class QuizSet(BaseModel):
    questions: list[QuizQuestion]

class QuizData(BaseModel):
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


def fetch_local_knowledge(query):
    """
    LangChain RAG Retrieval: Fetches content from the Chroma vector store.
    """
    try:
        persist_directory = os.path.join(os.path.dirname(__file__), 'chroma_db')
        if os.path.exists(persist_directory):
            embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
                api_key=OPEN_AI_KEY
            )
            vectorstore = Chroma(persist_directory=persist_directory, embedding_function=embeddings)

            # Perform similarity search
            # Increased k to 3 to retrieve more context from full articles
            docs = vectorstore.similarity_search(query, k=3)
            if docs:
                combined_content = "\n\n".join([doc.page_content for doc in docs])
                return combined_content, "LangChain RAG"
        return "", ""
    except Exception as e:
        print(f"Error fetching local knowledge via LangChain: {e}")
        return "", ""


def fetch_wikipedia_article(query):
    try:
        return wikipedia.page(title=query, auto_suggest=False, preload=True).content, "Wikipedia"
    except Exception as e:
        print(f"Error fetching Wikipedia article: {e}")
        return "", ""

def get_ai_response(questions_count, article_title, difficulty="Medium", language="English"):
    if not OPEN_AI_KEY:
        raise RuntimeError("OpenAI API key is required.")

    client = OpenAI(api_key=OPEN_AI_KEY)

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
    if not article_content:
        raise RuntimeError(f"Could not find content for topic: {article_title}")

    # Using Structured Outputs with beta.chat.completions.parse
    print(f"article_content{article_content}")
    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"You are a quiz generator for a {difficulty} audience. Adjust the complexity, vocabulary, and trickiness of the facts accordingly. The entire quiz MUST be in {language}."},
            {"role": "user", "content": (
                f"Analyze the following Wikipedia article: {article_content}\n\n"
                f"Task: Generate exactly {questions_count} quiz sets for a {difficulty} level in {language}. Each set must have 3 real facts and 1 fake fact.\n"
            )}
        ],
        # temperature=1.5,
        response_format=QuizData,
    )

    # Extract the parsed data
    # print("Gemini response")
    # print(google_response)
    print("GPT response")
    print(response)
    quiz_data_obj = response.choices[0].message.parsed
    # quiz_data_obj = google_response.parsed
    print(quiz_data_obj)
    
    # Convert the Pydantic object back to the format expected by the rest of the app
    # The app expects a list of dictionaries where {statement: boolean}
    formatted_quiz_data = []
    for quiz_set in quiz_data_obj.quiz_sets:
        question_dict = {}
        for q in quiz_set.questions:
            question_dict[q.statement] = q.is_real
        formatted_quiz_data.append(question_dict)
        
    return formatted_quiz_data

def game_logic(questions_count, topic, username):
    error_message = show_error_message(4, ":")
    final_score = 0
    try:
        facts = get_ai_response(questions_count, topic)
    except Exception as e:
        print(f"Error starting game: {e}")
        return

    print(f"\nWhich of these is not a fact about {topic}? ")

    for fact in facts:
        list_of_facts = list(fact.items())
        random.shuffle(list_of_facts)
        shuffled_facts = dict(list_of_facts)
        index = 0
        correct_fact = ""
        temp_list_of_facts = []
        print()
        
        for question, answer in shuffled_facts.items():
            index += 1
            print(f"{index}-{question}")
            temp_list_of_facts.append(f"{index}-{question}")
            if shuffled_facts[question] is False:
                correct_fact = question

        user_input = input("\nPlease choose an option between 1 and 4: ")
        while user_input not in ["1", "2", "3", "4"]:
            user_input = input(f"\n{error_message}")

        for value in temp_list_of_facts:
            if user_input in value:
                if shuffled_facts[value[2:]] is False:
                    final_score += 1
                    print(COLOR_FORMAT_MAP["bright_green"][0] + f"Well done {username} that is correct!" + COLOR_FORMAT_MAP["bright_green"][1])
                    break
                else:
                    print(COLOR_FORMAT_MAP["orange_text"][0] + f"Sorry {username} that is not correct!" + COLOR_FORMAT_MAP["orange_text"][1])
                    print(COLOR_FORMAT_MAP["info"][0] + f"\nThe correct answer was: {correct_fact}" + COLOR_FORMAT_MAP["info"][1])
                    break

    print(f"\nThank you for playing our game {username}, your final score is {round((final_score * 100) / questions_count)}%.")

def get_ai_analysis(user_history_json, language="English"):
    if not OPEN_AI_KEY:
        raise RuntimeError("OpenAI API key is required.")
    
    client = OpenAI(api_key=OPEN_AI_KEY)

    response = client.beta.chat.completions.parse(
        model="gpt-5-mini",
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
