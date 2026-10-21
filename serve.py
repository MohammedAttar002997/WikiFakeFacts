"""Run WikiFakeFact with a production server (waitress).

    python serve.py

Settings (environment variables or .env):
    HOST  address to listen on (default 127.0.0.1; use 0.0.0.0 to accept connections from other machines)
    PORT  port to listen on (default 8000)
    SECRET_KEY must be set to a long random value; the server won't start with the development default.
"""
import os
import sys

from waitress import serve

from app import DEV_SECRET_KEY, app, init_database


def main():
    if app.secret_key == DEV_SECRET_KEY:
        sys.exit('SECRET_KEY is not set. Add a long random value to .env, for example the output of:\n'
                 '    python -c "import secrets; print(secrets.token_hex(32))"')
    if os.environ.get('FLASK_DEBUG') == '1':
        sys.exit('Debug mode is on (FLASK_DEBUG=1). Turn it off before serving the app to other people.')

    with app.app_context():
        init_database()

    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 8000))
    print(f'Serving WikiFakeFact on http://{host}:{port}')
    serve(app, host=host, port=port)


if __name__ == '__main__':
    main()
