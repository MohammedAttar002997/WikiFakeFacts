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
python app.py
```

The application should now be running at `http://127.0.0.1:5003/`. On startup it creates `quiz.sqlite` if needed and applies any pending database migrations.

## Database migrations

The schema is managed with [Flask-Migrate](https://flask-migrate.readthedocs.io/) (Alembic); migration scripts live in `migrations/versions/`.

- **Applying migrations** happens automatically when you start the app with `python app.py`. To apply them by hand: `flask --app app db upgrade`.
- **Changing the schema:** edit the models in `models.py`, then generate a migration and review it before committing:

  ```bash
  flask --app app db migrate -m "describe the change"
  ```

- **Checking** that the database matches the models: `flask --app app db check`.

Databases created before migrations were added are detected on startup, brought up to the baseline schema, and stamped, so existing data is kept.

Set `DATABASE_URL` to use a different database (for example `sqlite:///other.sqlite`); it defaults to `quiz.sqlite` next to `app.py`.

## Running the tests

```bash
python -m pytest            # everything (about 10 seconds)
python -m pytest -m "not slow"   # skip the migration tests, which start the app in a subprocess
```

The tests use a throwaway database and stub every OpenAI, Wikipedia and web request, so they're free and run offline.

## Translations

The interface is available in English, Spanish, French, German, Italian and Portuguese; players switch with the language menu in the navbar (it also sets the default quiz language).

- Write UI text in English and wrap it: `{{ _('Play now') }}` in templates, `_("…")` in `app.py`. Use `{{ _n('{n} game', '{n} games', count) }}` for plurals and `.format(name=…)` for values.
- Translations live in `locales/<lang>.json`, keyed by the English text. Missing strings fall back to English.
- Run `python translations.py` to list strings that still need translating. Translations must not contain HTML or ASCII double quotes; `tests/test_i18n.py` checks this and that every string is translated.

## Usage

Navigate to the application in your web browser, select a category, difficulty, and language to start playing. Explore the 'My Progress' dashboard for AI-powered insights into your quiz performance.
