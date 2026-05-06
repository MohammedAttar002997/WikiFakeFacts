# WikiFakeFact: AI-Powered Quiz Game

WikiFakeFact is an AI-powered quiz game designed to test players' knowledge with a mix of real and fake facts. The game features multiple difficulty levels, multilingual support, and an AI-powered insights dashboard.

## Features

*   **AI-Powered Quiz Generation**: Utilizes OpenAI's GPT models to generate quizzes with structured outputs.
*   **Robust RAG System**: Integrates LangChain with OpenAI's `text-embedding-3-small` for a Retrieval-Augmented Generation (RAG) system, providing high-quality fallback content when Wikipedia is unavailable.
*   **Multilingual Support**: Quizzes available in English, Spanish, French, German, Italian, and Portuguese.
*   **Difficulty Levels**: Caters to audiences from Ages 7-99.
*   **My Progress Dashboard**: Provides AI-powered insights into player performance.
*   **Players Directory**: Public profiles for players.
*   **Knowledge Base**: Contains full-length articles for seven categories: Sports, Animals, Countries, Fruits, Planets/Space, Technologies, Musical Instruments.

## Project Structure

```
hackaton_project/
├── hackaton/
│   ├── app.py              # Main Flask application
│   ├── data.py             # Core AI logic (quiz generation, RAG retrieval)
│   ├── rag_indexer.py      # Script for indexing knowledge base into ChromaDB
│   ├── knowledge_base.json # Local store of full-length articles
│   ├── models.py           # SQLAlchemy models
│   ├── templates/          # HTML templates
│   ├── static/             # Static assets (CSS, JS)
│   └── ...                 # Other supporting files
├── README.md               # This file
└── requirements.txt        # Python dependencies
```

## Setup and Installation

### 1. Clone the Repository

```bash
git clone <repository_url>
cd hackaton_project
```

### 2. Create a Virtual Environment (Recommended)

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure OpenAI API Key

Create a `.env` file in the `hackaton/` directory with your OpenAI API key:

```
OPENAI_API_KEY="your_openai_api_key_here"
# Optional: If you are using a custom OpenAI base URL, uncomment and set it:
# OPENAI_BASE_URL="https://api.openai.com/v1"
```

### 5. Generate the ChromaDB Vector Store

This step is crucial for the RAG system to function. The `rag_indexer.py` script will process the `knowledge_base.json` and create the `chroma_db` directory.

**Important**: Ensure you have direct access to the OpenAI API (i.e., no proxy issues) when running this script.

```bash
cd hackaton
python rag_indexer.py
cd ..
```

This will create a `chroma_db` directory inside `hackaton/` containing the indexed knowledge base.

### 6. Run the Flask Application

```bash
export FLASK_APP=hackaton/app.py
flask run
```

The application should now be running at `http://127.0.0.1:5000/`.

## Usage

Navigate to the application in your web browser, select a category, difficulty, and language to start playing. Explore the 'My Progress' dashboard for AI-powered insights into your quiz performance.
