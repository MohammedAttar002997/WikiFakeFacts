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

Flask app. `app.py` holds the core: config, auth, rate limits, error logging, the quiz flow, accounts and the pages. Three self-contained areas live in `views/` and add their routes to the same `app` when `app.py` imports them on its last line, so endpoint names (`url_for('admin')`, `url_for('live_match')`) are unchanged: `views/admin.py` (/admin, report actions, /health, the `flask reports|errors|reset-pin|delete-player` commands), `views/live.py` (live head-to-head routes) and `views/timers.py` (ready-made quizzes, push reminders, the `/cron/*` routes). They import what they need from `app`, except `utcnow` and `get_ai_response`, which they call as `core.utcnow()` / `core.get_ai_response()` so a stub set on `app` (as the tests do) applies to them too. When adding a route to one of those areas, put it in its module; a new area of similar size gets its own module. `translations.find_missing` also scans `views/`. Other modules are helpers that `app.py` imports:

- `data.py`: quiz generation and the progress analysis, using OpenAI structured outputs (pydantic models). Article lookup for a quiz goes through three levels: Wikipedia, then the knowledge-base fallback, then the model's own knowledge. The knowledge-base fallback searches the `knowledge_chunk` table (embeddings made by `rag_indexer.py`, nearest passages found with numpy). If the table is empty, it uses the whole saved article from `knowledge_base.json` (refreshed by `build_knowledge_base.py`).
- `live.py`: rules of the live head-to-head (`/live/<code>`) as pure functions over the match state; `app.py` loads the `live_match` row, moves it on to the current time and writes it back only if its `version` is unchanged. There's no background job and no websocket: both pages poll `/live/<code>/state`.
- `ready_pool.py`: ready-made quizzes, so a game can start without waiting ~20 s for the AI. `/start_quiz` records the kind asked for (category, difficulty, rounds, language) in `quiz_demand` and takes a matching `ready_quiz` if one is in stock (any category for "Surprise me"), otherwise generates as before. `/cron/refill` (Bearer `CRON_SECRET`; called by Vercel's daily cron in `vercel.json` and every 30 min by `.github/workflows/refill.yml`) tops each recently wanted kind up to 2, at most 20 a day, and stops starting new ones after 25 s. A ready quiz goes to one player only: it's taken by an `UPDATE ... WHERE used_at IS NULL`.
- `fake_diff.py`: "what the fake changed" in the answer review. It compares a round's fake statement with its verified source quote word by word and marks the changed detail in both (and lists it, e.g. `4.1 → 1`). It returns None, so the quote shows plainly as before, when they don't line up: under 60% shared words, more than 3 changes, a change over 4 words (a rewrite), or a quote in another language. `review_round()` in `app.py` builds every review row (results, `/review`, the guest round), so all of them get it.
- `health.py`: what breaks the site quietly. `/health` (public) reports the database's and the code's migration; `/admin` shows a red banner when the database is behind, and a warning when a timer is overdue (every `/cron/*` call is recorded in `cron_run`; refill expected every 2 h, reminder every 26 h, only when set up). `.github/workflows/migration-applied.yml` runs `scripts/check_migration_applied.py` on pull requests that add a migration and fails until production has it.
- `practice.py`: "Practice your mistakes" (`POST /practice/mistakes`; offered on `/history` and on results when there are any). A quiz of up to 5 rounds the player got wrong or timed out on, newest first, no AI call. Saved as a `QuizSession` with `practice=True`: it earns no `Score`, badges or leaderboard points and has no share link. A round answered right in practice, or hidden after a report, isn't offered again. Not the same as the older `POST /practice` ("practice your weakest topic" from `/analysis`, a fresh AI quiz).
- `reminders.py`: daily-challenge push reminders. Players switch them on from `/daily` (`app.js` subscribes through the service worker and posts to `/push/subscribe`; only endpoints of known push services are stored, at most 5 devices per player). Vercel's cron calls `/cron/daily-reminder` at 16:00 UTC, plus a catch-up call at 17:00 (Bearer `CRON_SECRET`). It sends, 10 at a time in threads (database rows stay in the request's thread; the threads get plain `Device` tuples), and starts no new batch after 200 s, one notification, in the language the player had when switching on, to everyone who hasn't finished today's challenge, and deletes devices the push service reports as gone (404/410). Needs `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` (made once with `python make_vapid_keys.py`); without them the option isn't shown. `templates/sw.js` shows the notification and opens `/daily` on click.
- `hidden_rounds.py`: rounds the owner hid from `/admin` after a report (`hidden_round` table, matched by a fingerprint of the four statements in any order). The daily challenge leaves them out, the guest round never picks them, and a quiz containing one can't be replayed as a challenge (answers are compared round by round). Fresh AI quizzes need no check.
- `admin_stats.py`: the numbers on `/admin`, read-only. The page exists only for players named in `ADMIN_USERS` (Vercel env, comma-separated, case-insensitive); everyone else gets a 404. It's English-only on purpose (not wrapped in `_()`).
- PWA: `public/static/manifest.webmanifest`, icons in `public/static/img/icons/`, and the service worker `templates/sw.js`, served at `/sw.js` by `app.py` (not in `public/`: on Vercel that folder is CDN-only and missing from the function) so it covers the whole site. It only keeps `/static/` files (network first) and the `/offline` page; never cache pages, quiz state or anything with player data in it. Bump `CACHE` in `sw.js` when changing what it precaches.
- `chatbot.py`: the help chat. `content_extractor.py`: text from uploads (pdf/txt/docx) and URLs for custom quizzes. `badges.py`: badges and streaks. `default_text.py`: categories, topics, difficulty, game-length and language options.
- Topics: `MENU` holds the 20 hand-picked topics per category, the only ones with a knowledge-base copy; the daily challenge and weekly events use only these. `TOPICS` adds ~4,900 more from Wikipedia's Vital articles, including four categories with no `MENU` topics at all (History, Science, Famous People, Cities) (`topics.json`, made by `python build_topic_list.py`) and is what quizzes, "Surprise me", leaderboard categories and badges use. A quiz on a non-`MENU` topic skips the knowledge-base step when Wikipedia fails.
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
