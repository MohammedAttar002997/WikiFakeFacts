"""Apply the database migrations to the hosted (Vercel / Neon) database from your own machine.

    vercel env pull .env.production.local --environment=production
    python migrate_production.py

Reads DATABASE_URL_UNPOOLED (or DATABASE_URL) from .env.production.local, or from the file given as the first
argument, and never prints it. Uses psycopg like the app does; where Windows Smart App Control blocks psycopg's
compiled library, it falls back to pg8000, a pure-Python driver (pip install -r requirements-dev.txt).
"""
import os
import ssl
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import dotenv_values

# Options libpq understands but pg8000 doesn't; pg8000 gets TLS through an SSL context instead
LIBPQ_ONLY_OPTIONS = ('sslmode', 'channel_binding')


def pg8000_url(url):
    """The same database URL for SQLAlchemy's pg8000 dialect."""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k not in LIBPQ_ONLY_OPTIONS])
    return urlunsplit(('postgresql+pg8000', parts.netloc, parts.path, query, ''))


def use_pg8000_with_tls():
    """Make every pg8000 connection use TLS that checks the server's certificate, as sslmode=require would."""
    import pg8000.dbapi
    connect = pg8000.dbapi.connect

    def connect_with_tls(*args, **kwargs):
        kwargs.setdefault('ssl_context', ssl.create_default_context())
        return connect(*args, **kwargs)

    pg8000.connect = pg8000.dbapi.connect = connect_with_tls


def psycopg_loads():
    try:
        import psycopg  # noqa: F401
        return True
    except ImportError:
        return False


def main():
    env_file = sys.argv[1] if len(sys.argv) > 1 else '.env.production.local'
    if not os.path.exists(env_file):
        sys.exit(f'{env_file} not found. Download the production settings first:\n'
                 f'    vercel env pull {env_file} --environment=production')
    settings = dotenv_values(env_file)
    url = settings.get('DATABASE_URL_UNPOOLED') or settings.get('DATABASE_URL')
    if not url or not url.startswith(('postgres://', 'postgresql://')):
        sys.exit(f'No Postgres DATABASE_URL in {env_file}.')

    if psycopg_loads():
        os.environ['DATABASE_URL'] = url
    else:
        print('psycopg is blocked or missing here, so using pg8000 instead.')
        use_pg8000_with_tls()
        os.environ['DATABASE_URL'] = pg8000_url(url)

    # Imported only now: the app reads DATABASE_URL when it's imported
    from flask_migrate import check, current, upgrade
    from app import app

    with app.app_context():
        upgrade()
        print('Database is at:')
        current()
        check()


if __name__ == '__main__':
    main()
