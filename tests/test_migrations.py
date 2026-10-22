"""Migrations build the same schema for fresh databases and databases from before migrations existed.

Each case runs in a subprocess because the app binds to its database when it's imported."""
import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLASK = os.path.join(os.path.dirname(sys.executable), 'flask')

# models.py as it was before migrations (on main): created with create_all(), no later columns or tables
PRE_MIGRATION_SCHEMA = """
CREATE TABLE user (id INTEGER NOT NULL, username VARCHAR(80) NOT NULL, pin_hash VARCHAR(255), created_at DATETIME,
                   PRIMARY KEY (id), UNIQUE (username));
CREATE TABLE score (id INTEGER NOT NULL, user_id INTEGER NOT NULL, topic VARCHAR(100) NOT NULL,
                    difficulty VARCHAR(50) NOT NULL, score INTEGER NOT NULL, total_questions INTEGER NOT NULL,
                    percentage FLOAT NOT NULL, timestamp DATETIME, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES user (id));
CREATE TABLE quiz_session (id INTEGER NOT NULL, user_id INTEGER NOT NULL, topic VARCHAR(100) NOT NULL,
                           difficulty VARCHAR(50) NOT NULL, questions_json TEXT NOT NULL, current_question_index INTEGER,
                           score INTEGER, total_questions INTEGER NOT NULL, is_completed BOOLEAN, PRIMARY KEY (id),
                           FOREIGN KEY(user_id) REFERENCES user (id));
CREATE TABLE custom_content (id INTEGER NOT NULL, user_id INTEGER NOT NULL, title VARCHAR(200) NOT NULL,
                             content TEXT NOT NULL, content_type VARCHAR(20) NOT NULL, source VARCHAR(500),
                             created_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(user_id) REFERENCES user (id));
CREATE TABLE chatbot_session (id INTEGER NOT NULL, session_id VARCHAR(100) NOT NULL, conversation_json TEXT NOT NULL,
                              created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id), UNIQUE (session_id));
INSERT INTO user (username) VALUES ('OldPlayer');
INSERT INTO score (user_id, topic, difficulty, score, total_questions, percentage) VALUES (1, 'Cat', 'Easy', 2, 3, 66.7);
"""


def run_init(db_path):
    env = {**os.environ, 'DATABASE_URL': 'sqlite:///' + db_path.replace('\\', '/')}
    script = textwrap.dedent("""
        from app import app, init_database
        with app.app_context():
            init_database()
            init_database()  # running twice is harmless
    """)
    subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env, check=True, capture_output=True)
    check = subprocess.run([FLASK, '--app', 'app', 'db', 'check'], cwd=ROOT, env=env, capture_output=True, text=True)
    return check.returncode, check.stdout + check.stderr


def read(db_path, query):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(query).fetchall()


@pytest.mark.slow
def test_fresh_database_matches_models(tmp_path):
    db_path = str(tmp_path / 'fresh.sqlite')
    code, output = run_init(db_path)
    assert code == 0, output
    assert read(db_path, 'SELECT version_num FROM alembic_version') == [('0008_player_profile',)]


@pytest.mark.slow
def test_pre_migration_database_is_upgraded_keeping_data(tmp_path):
    db_path = str(tmp_path / 'old.sqlite')
    with sqlite3.connect(db_path) as conn:
        conn.executescript(PRE_MIGRATION_SCHEMA)
    code, output = run_init(db_path)
    assert code == 0, output  # no differences left between the upgraded database and the models
    assert read(db_path, 'SELECT username FROM user') == [('OldPlayer',)]
    assert read(db_path, 'SELECT topic, score FROM score') == [('Cat', 2)]
    assert read(db_path, 'SELECT version_num FROM alembic_version') == [('0008_player_profile',)]
