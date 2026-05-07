import os
import json
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


load_dotenv()
OPEN_AI_KEY = os.getenv("OPENAI_API_KEY")
def index_knowledge_base():
    """
    Loads the local knowledge base, splits it into chunks, and stores it in a Chroma vector store using OpenAI embeddings.
    """
    kb_path = os.path.join(os.path.dirname(__file__), 'knowledge_base.json')
    persist_directory = os.path.join(os.path.dirname(__file__), 'chroma_db')

    if not os.path.exists(kb_path):
        print("Knowledge base file not found.")
        return

    with open(kb_path, 'r') as f:
        kb = json.load(f)

    documents = []
    for topic, content in kb.items():
        # Create a LangChain Document for each topic
        doc = Document(
            page_content=content,
            metadata={"topic": topic}
        )
        documents.append(doc)

    # Split documents into chunks
    # Increased chunk size and overlap for better context retention with full articles
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    splits = text_splitter.split_documents(documents)

    # Initialize OpenAI Embeddings
    # Using the pre-configured environment variables for OpenAI
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=OPEN_AI_KEY
    )

    # Remove existing index if it exists to start fresh
    if os.path.exists(persist_directory):
        import shutil
        shutil.rmtree(persist_directory)

    # Create and persist the vector store
    vectorstore = Chroma.from_documents(
        documents=splits,
        embedding=embeddings,
        persist_directory=persist_directory
    )

    print(f"Successfully indexed {len(documents)} topics into {len(splits)} chunks using text-embedding-3-small.")
    return vectorstore


if __name__ == "__main__":
    index_knowledge_base()
