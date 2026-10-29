# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Windows, Python 3.15 (CI version), virtualenv in `.venv`. Use `.venv/Scripts/python.exe` when the venv isn't activated.

```bash
pip install -r requirements-dev.txt        # app deps (pinned) + pytest + pg8000
python app.py                              # dev server on :5003; applies pending migrations on startup
python serve.py                            # production server (waitress) on :8000; refuses DEV_SECRET_KEY or FLASK_DEBUG=1

python -m pytest                           # full suite (~10 s, offline, no API key needed)
python -m pytest -m "not slow"             # skip migration tests that start the app in a subprocess
python -m pytest tests/test_quiz.py::test_name   # single test

flask --app app db migrate -m "describe the change"   # after editing models.py; review the generated script
flask --app app db upgrade / db check
python translations.py                     # list UI strings still missing a translation
flask --app app errors                     # newest saved unexpected errors (local DB)
```

There is no linter or build step. CI (`.github/workflows/tests.yml`) runs `python -m pytest` on every push and PR.

## Architecture

Flask app, almost all of it in `app.py`: routes, auth, rate limits, error logging, CLI commands (`reset-pin`, `delete-player`, `errors`). Other modules are helpers that `app.py` imports:

- `data.py`: quiz generation and the progress analysis, using OpenAI structured outputs (pydantic models). Article lookup for a quiz goes through three levels: Wikipedia, then the knowledge-base fallback, then the model's own knowledge. The knowledge-base fallback searches the `knowledge_chunk` table (embeddings made by `rag_indexer.py`, nearest passages found with numpy). If the table is empty, it uses the whole saved article from `knowledge_base.json` (refreshed by `build_knowledge_base.py`).
- `chatbot.py`: the help chat. `content_extractor.py`: text from uploads (pdf/txt/docx) and URLs for custom quizzes. `badges.py`: badges and streaks. `default_text.py`: categories, topics, difficulty, game-length and language options.
- `translations.py` + `locales/<lang>.json`: UI text is English, wrapped in `_()` / `_n()`. Catalogs are keyed by the English string. `tests/test_i18n.py` fails if any string is untranslated or a translation contains HTML or ASCII double quotes. Adding UI text therefore means adding it to every locale (es, fr, de, it, pt, ar; Arabic is RTL).
- Models are in `models.py`. The schema is managed only through Alembic migrations in `migrations/versions/`, numbered `00NN_name.py`. `init_database()` in `app.py` also upgrades pre-migration databases to the baseline.
- `DATABASE_URL` selects the DB: SQLite `quiz.sqlite` by default, Neon Postgres on Vercel (rewritten to `postgresql+psycopg://`).

Players sign in with a name and a 4-digit PIN (no passwords or email). Every POST needs a CSRF token: a hidden `csrf_token` field in forms, or the `X-CSRFToken` header for fetch.

### Tests

`tests/conftest.py` sets `DATABASE_URL` to a throwaway SQLite DB built by the real migrations, before it imports `app`. It also disables CSRF, except in `test_csrf.py`. An autouse `ai` fixture monkeypatches `app.get_ai_response`, `app.get_ai_analysis` and `app.extract_text_from_url`, and tables are emptied after each test. Stub new external calls the same way, by patching the name inside `app`, not inside `data`.

## Deployment (Vercel + Neon)

- Every merge to `main` deploys to production, and every branch gets a preview. `app.py` runs as a serverless function. `public/static/` is served by the CDN; locally Flask serves it at `/static`.
- **Migrations don't run on Vercel.** When a PR adds a migration, apply it to production from that branch before merging: `vercel env pull .env.production.local --environment=production`, then `python migrate_production.py`. Keep migrations additive (new tables or nullable columns), because the old code is still live in the meantime.
- After changing the knowledge base or `OPENAI_EMBEDDING_MODEL`, run `python rag_indexer.py --production`.
- `python production_errors.py [-n 50 --full]` reads the `error_log` table on production. Vercel Hobby keeps runtime logs for only 30 minutes.
- `.vercelignore` excludes tests, `.env*`, SQLite files and uploads from deployments.

## Windows Smart App Control

Smart App Control blocks compiled DLLs, so:
- If SQLAlchemy fails with "An Application Control policy has blocked this file", reinstall it pure-Python. Set `$env:DISABLE_SQLALCHEMY_CEXT = "1"`, then run `pip install --force-reinstall --no-deps --no-binary sqlalchemy sqlalchemy==<pinned version>`. Repeat after anything reinstalls SQLAlchemy.
- psycopg may be blocked. `migrate_production.py` (and `rag_indexer.py --production`, which uses it) then falls back to `pg8000` with TLS on its own.
