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
WikiFakeFacts/
├── app.py                  # Flask application: routes, accounts, admin commands
├── data.py                 # AI quiz generation and analysis, Wikipedia / knowledge-base lookup
├── badges.py               # Badge rules and daily streaks
├── translations.py         # UI translation helpers; catalogs in locales/
├── models.py               # SQLAlchemy models
├── migrations/             # Database migrations (Flask-Migrate / Alembic)
├── templates/, static/     # HTML templates, CSS and JavaScript
├── locales/                # Translations (es, fr, de, it, pt)
├── tests/                  # pytest suite
├── rag_indexer.py          # Optional: builds the local knowledge-base index (chroma_db/)
├── knowledge_base.json     # Articles for the knowledge-base fallback
├── requirements.txt        # Pinned app dependencies
├── requirements-dev.txt    # + test tools
└── requirements-rag.txt    # + optional knowledge-base fallback
```

## Setup and Installation

### 1. Clone the Repository

```bash
git clone https://github.com/MohammedAttar002997/WikiFakeFacts.git
cd WikiFakeFacts
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux
```

### 3. Install Dependencies

Versions are pinned so every install gets the same, tested set:

```bash
pip install -r requirements.txt       # to run the app
pip install -r requirements-dev.txt   # to also run the tests
```

On Windows, if starting the app fails with *"An Application Control policy has blocked this file"*, see [Troubleshooting](#troubleshooting).

### 4. Configure the Environment

Create a `.env` file next to `app.py`:

```
OPENAI_API_KEY="your_openai_api_key_here"
SECRET_KEY="a long random string"   # signs login cookies; generate one with: python -c "import secrets; print(secrets.token_hex(32))"
# Optional: a custom OpenAI base URL
# OPENAI_BASE_URL="https://api.openai.com/v1"
# Optional: which OpenAI models to use (these are the defaults)
# OPENAI_QUIZ_MODEL="gpt-4o-mini"
# OPENAI_ANALYSIS_MODEL="gpt-5-mini"
# OPENAI_CHAT_MODEL="gpt-4o-mini"
# OPENAI_EMBEDDING_MODEL="text-embedding-3-small"
```

### 5. Optional: Build the Local Knowledge Base

When Wikipedia is unavailable, quizzes fall back to a local knowledge base, and if that isn't set up either, to the AI model's own knowledge. The knowledge base needs `chromadb`, which doesn't install on Python 3.15 yet; on Python 3.12 or 3.13:

```bash
pip install -r requirements-rag.txt
python rag_indexer.py      # creates chroma_db/ from knowledge_base.json
```

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

## Player accounts

Players sign in with a name and a 4-digit PIN. On the **Account** page (linked from the home page and My Progress) they can change their PIN or delete their account, which removes their scores, quizzes, uploads, badges, saved analysis and help chat.

For whoever runs the game:

```bash
flask --app app reset-pin "Phoenix"              # sets and prints a random new PIN
flask --app app reset-pin "Phoenix" --pin 1234   # or a PIN you choose
flask --app app delete-player "Phoenix"          # removes the player and all their data
```

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

## Troubleshooting

**Windows: "DLL load failed … An Application Control policy has blocked this file" when starting the app.**
Smart App Control blocks SQLAlchemy's optional compiled speed-ups. Reinstall SQLAlchemy without them (it then uses its pure-Python code, which is plenty fast for this app). In PowerShell:

```powershell
$env:DISABLE_SQLALCHEMY_CEXT = "1"
pip install --force-reinstall --no-deps --no-binary sqlalchemy sqlalchemy==2.1.5
```

Run this again after anything reinstalls or upgrades SQLAlchemy.

## Usage

Navigate to the application in your web browser, select a category, difficulty, and language to start playing. Explore the 'My Progress' dashboard for AI-powered insights into your quiz performance.
