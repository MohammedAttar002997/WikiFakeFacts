"""serve.py refuses unsafe settings before starting the production server."""
import pytest

import serve


def test_refuses_the_development_secret_key(monkeypatch):
    monkeypatch.setattr(serve.app, 'secret_key', serve.DEV_SECRET_KEY)
    with pytest.raises(SystemExit, match='SECRET_KEY'):
        serve.main()


def test_refuses_debug_mode(monkeypatch):
    monkeypatch.setattr(serve.app, 'secret_key', 'a-real-secret')
    monkeypatch.setenv('FLASK_DEBUG', '1')
    with pytest.raises(SystemExit, match='Debug mode'):
        serve.main()


def test_starts_waitress_with_host_and_port(monkeypatch):
    started = {}
    monkeypatch.setattr(serve.app, 'secret_key', 'a-real-secret')
    monkeypatch.delenv('FLASK_DEBUG', raising=False)
    monkeypatch.setenv('HOST', '0.0.0.0')
    monkeypatch.setenv('PORT', '9123')
    monkeypatch.setattr(serve, 'serve', lambda app, host, port: started.update(host=host, port=port))
    serve.main()
    assert started == {'host': '0.0.0.0', 'port': 9123}


def test_session_cookie_is_same_site_lax():
    assert serve.app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'
