"""Limits on what one visitor can cost or reach: rate limits, content size, safe URL fetching and file parsing,
and errors that don't leak internals."""
import io
import socket
import zipfile
from datetime import timedelta
from types import SimpleNamespace

import pytest
from docx import Document

import app as app_module
import content_extractor
from app import db
from content_extractor import (MAX_CONTENT_CHARS, ContentError, check_public_address, extract_text_from_upload,
                               extract_text_from_url)
from models import CustomContent, RateLimit, User
from translations import translate

FORM = {'length': '1', 'difficulty': '2', 'language': 'en'}


def resolve_to(monkeypatch, addresses):
    """Make DNS answer from `addresses` ({hostname: ip}) without touching the network."""
    def getaddrinfo(host, port, *args, **kwargs):
        ip = addresses.get(host, host)
        family = socket.AF_INET6 if ':' in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, '', (ip, port))]
    monkeypatch.setattr(content_extractor.socket, 'getaddrinfo', getaddrinfo)


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=(b'<p>' + b'Facts about volcanoes. ' * 20 + b'</p>',)):
        self.status_code = status
        self.headers = {'Content-Type': 'text/html', **(headers or {})}
        self.chunks = chunks
        self.chunks_read = 0

    @property
    def is_redirect(self):
        return 'Location' in self.headers and self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for chunk in self.chunks:
            self.chunks_read += 1
            yield chunk

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def serve(monkeypatch, responses):
    """Answer requests.get with `responses` ({url: FakeResponse}); returns the list of URLs fetched."""
    fetched = []

    def get(url, **kwargs):
        assert kwargs.get('allow_redirects') is False  # Redirects are followed by hand, checking each address
        fetched.append(url)
        return responses[url]
    monkeypatch.setattr(content_extractor.requests, 'get', get)
    return fetched


# ---------- Fetching pages ----------------------------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'http://127.0.0.1/', 'http://localhost/admin', 'http://169.254.169.254/latest/meta-data/', 'http://10.0.0.5/',
    'http://192.168.1.1/', 'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://0.0.0.0/',
    'file:///etc/passwd', 'ftp://example.com/x', 'gopher://example.com/',
])
def test_non_public_addresses_are_refused(url, monkeypatch):
    resolve_to(monkeypatch, {'localhost': '127.0.0.1'})
    with pytest.raises(ContentError) as error:
        check_public_address(url)
    assert error.value.reason == 'blocked_url'


def test_a_hostname_pointing_inside_is_refused(monkeypatch):
    resolve_to(monkeypatch, {'internal.example.com': '10.1.2.3'})
    with pytest.raises(ContentError, match='blocked_url'):
        extract_text_from_url('https://internal.example.com/secrets')


def test_redirects_are_checked_too(monkeypatch):
    resolve_to(monkeypatch, {'public.example': '93.184.216.34'})
    fetched = serve(monkeypatch, {
        'https://public.example/a': FakeResponse(302, {'Location': 'http://127.0.0.1:8080/admin'}),
    })
    with pytest.raises(ContentError, match='blocked_url'):
        extract_text_from_url('https://public.example/a')
    assert fetched == ['https://public.example/a']  # The internal address was never requested


def test_public_page_is_fetched_and_cleaned(monkeypatch):
    resolve_to(monkeypatch, {'public.example': '93.184.216.34'})
    serve(monkeypatch, {'https://public.example/a': FakeResponse(302, {'Location': '/b'}),
                        'https://public.example/b': FakeResponse()})
    assert extract_text_from_url('public.example/a').startswith('Facts about volcanoes.')


def test_downloads_stop_at_the_size_limit(monkeypatch):
    resolve_to(monkeypatch, {'big.example': '93.184.216.34'})
    page = FakeResponse(chunks=[b'x' * 65536] * 1000)  # 64 MB on offer
    serve(monkeypatch, {'https://big.example/': page})
    extract_text_from_url('https://big.example/')
    assert page.chunks_read * 65536 <= content_extractor.MAX_PAGE_BYTES + 65536


def test_slow_downloads_give_up(monkeypatch):
    resolve_to(monkeypatch, {'slow.example': '93.184.216.34'})
    serve(monkeypatch, {'https://slow.example/': FakeResponse(chunks=[b'a'] * 100)})
    clock = iter(range(0, 1000, 5))  # Each chunk "takes" five seconds
    monkeypatch.setattr(content_extractor.time, 'monotonic', lambda: next(clock))
    with pytest.raises(ContentError, match='fetch_failed'):
        extract_text_from_url('https://slow.example/')


def test_non_pages_are_refused(monkeypatch):
    resolve_to(monkeypatch, {'files.example': '93.184.216.34'})
    serve(monkeypatch, {'https://files.example/x': FakeResponse(headers={'Content-Type': 'application/zip'})})
    with pytest.raises(ContentError, match='fetch_failed'):
        extract_text_from_url('https://files.example/x')


# ---------- Reading uploads -------------------------------------------------------------------------------------

def docx_bytes(text):
    output = io.BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(output)
    return output.getvalue()


def test_docx_text_is_read(app_ctx):
    assert extract_text_from_upload(docx_bytes('Volcanoes erupt.'), 'notes.docx') == 'Volcanoes erupt.'


def test_docx_zip_bomb_is_refused(monkeypatch):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', b'0' * (content_extractor.MAX_DOCX_UNPACKED_BYTES + 1))
    assert len(output.getvalue()) < 200_000  # Tiny on the wire…
    with pytest.raises(ContentError) as error:
        extract_text_from_upload(output.getvalue(), 'bomb.docx')
    assert error.value.reason == 'unreadable_file'


@pytest.mark.parametrize('data, filename, reason', [
    (b'%PDF-1.4 this is not really a pdf', 'broken.pdf', 'unreadable_file'),
    (b'not a zip', 'broken.docx', 'unreadable_file'),
    (b'MZ\x90\x00', 'program.exe', 'unsupported_file'),
])
def test_bad_files_are_refused_politely(data, filename, reason):
    with pytest.raises(ContentError) as error:
        extract_text_from_upload(data, filename)
    assert error.value.reason == reason


def test_text_is_cut_to_the_maximum():
    text = extract_text_from_upload(('word ' * MAX_CONTENT_CHARS).encode(), 'long.txt')
    assert len(text) == MAX_CONTENT_CHARS


def test_text_files_in_other_encodings_still_load():
    assert 'Caf' in extract_text_from_upload('Café '.encode('latin-1') * 50, 'notes.txt')


# ---------- Custom quiz route -----------------------------------------------------------------------------------

def upload(client, data, filename, **form):
    return client.post('/custom_quiz', data={**FORM, **form, 'file': (io.BytesIO(data), filename)},
                       content_type='multipart/form-data')


def test_upload_makes_a_quiz_without_saving_files(login, app_ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = login('Owner')
    response = upload(client, docx_bytes('Volcanoes erupt magma. ' * 20), 'notes.docx')
    assert response.headers['Location'] == '/quiz'
    assert CustomContent.query.one().source == 'notes.docx'
    assert list(tmp_path.iterdir()) == []


def test_errors_are_translated_and_hide_details(login, ai, monkeypatch):
    client = login('Owner', language='de')
    monkeypatch.setattr(app_module, 'extract_text_from_url',
                        lambda url: (_ for _ in ()).throw(ContentError('blocked_url', 'internal -> 10.0.0.5')))
    client.post('/custom_quiz', data={**FORM, 'url': 'http://internal/'})
    html = client.get('/custom_quiz').get_data(as_text=True)
    assert translate("That link can't be used. Please link to a public web page.", 'de') in html
    assert '10.0.0.5' not in html


def test_ai_failures_show_a_generic_message(login, ai, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('Error code: 429 - secret-org-id quota exceeded')
    monkeypatch.setattr(app_module, 'get_ai_response', broken)
    client = login('Player')
    client.post('/start_quiz', data={**FORM, 'category': '1'})
    html = client.get('/setup').get_data(as_text=True)
    assert "couldn&#39;t create your quiz" in html and 'secret-org-id' not in html


def test_long_titles_are_cut_to_fit(login, app_ctx):
    client = login('Owner')
    client.post('/custom_quiz', data={**FORM, 'title': 'T' * 300, 'url': 'https://example.com/v'})
    assert len(CustomContent.query.one().title) <= 200


# ---------- Rate limits -----------------------------------------------------------------------------------------

@pytest.fixture
def chatbot(monkeypatch):
    calls = []

    def respond(message, history):
        calls.append(message)
        return SimpleNamespace(message='Hi', suggested_action=None, action_url=None)
    monkeypatch.setattr(app_module, 'get_chatbot_response', respond)
    return calls


def test_chat_messages_are_limited_per_address(app, app_ctx, chatbot, monkeypatch):
    monkeypatch.setitem(app_module.RATE_LIMITS, 'chat', (2, timedelta(minutes=10)))
    statuses = [app.test_client().post('/api/chatbot', json={'message': 'hi'}).status_code for _ in range(3)]
    assert statuses == [200, 200, 429]  # A fresh browser (no cookie) each time doesn't get around it
    assert len(chatbot) == 2


def test_long_chat_messages_are_refused_unsaved(app, app_ctx, chatbot):
    response = app.test_client().post('/api/chatbot', json={'message': 'x' * 501})
    assert response.status_code == 400 and 'under 500 characters' in response.get_json()['error']
    assert chatbot == []
    from models import ChatbotSession
    assert ChatbotSession.query.count() == 0


def test_quiz_generation_is_limited_per_player(login, ai, monkeypatch):
    monkeypatch.setitem(app_module.RATE_LIMITS, 'quiz', (1, timedelta(hours=1)))
    client = login('Player')
    for _ in range(2):
        client.post('/start_quiz', data={**FORM, 'category': '1'})
    assert len(ai.quiz_calls) == 1
    assert 'going a bit fast' in client.get('/setup').get_data(as_text=True)


def test_new_accounts_are_limited_per_address(app, app_ctx, monkeypatch):
    monkeypatch.setitem(app_module.RATE_LIMITS, 'signup', (1, timedelta(hours=1)))
    client = app.test_client()
    client.post('/setup', data={'username': 'First', 'pin': '1234'})
    client.post('/setup', data={'username': 'Second', 'pin': '1234'})
    assert [u.username for u in User.query.all()] == ['First']
    client.post('/setup', data={'username': 'First', 'pin': '1234'})  # Signing in isn't limited
    from conftest import signed_in_name
    assert signed_in_name(client) == 'First'


def test_limits_start_over_after_the_window(app_ctx, monkeypatch):
    monkeypatch.setitem(app_module.RATE_LIMITS, 'chat', (1, timedelta(minutes=10)))
    with app_module.app.test_request_context():
        assert not app_module.over_limit('chat', 'someone')
        assert app_module.over_limit('chat', 'someone')
        db.session.get(RateLimit, 'chat:someone').window_start -= timedelta(minutes=11)
        db.session.commit()
        assert not app_module.over_limit('chat', 'someone')
