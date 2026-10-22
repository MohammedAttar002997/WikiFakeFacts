"""
Content Extractor Module
Handles extraction of text content from uploaded files (PDF, TXT, DOCX) and webpages.

Everything here deals with content from strangers, so it is bounded: uploads are read in memory (never written to
disk), text is cut at MAX_CONTENT_CHARS, and pages are only fetched from public addresses, with size, redirect and
time limits.
"""

import io
import ipaddress
import socket
import time
import zipfile
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from docx import Document
from pypdf import PdfReader

MIN_CONTENT_CHARS = 100
MAX_CONTENT_CHARS = 50_000  # Plenty for a quiz; more only adds storage and AI cost
MAX_DOCX_UNPACKED_BYTES = 50 * 1024 * 1024  # A .docx is a zip; refuse ones that expand to more than this
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
FETCH_DEADLINE = 15  # Seconds for a whole page download, however slowly the server sends it


class ContentError(Exception):
    """The upload or page can't be used; `reason` says why, for a message in the player's language."""

    def __init__(self, reason, detail=''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason  # 'unreadable_file', 'unsupported_file', 'blocked_url', 'fetch_failed', 'too_short'


def extract_text_from_pdf(stream):
    text = []
    length = 0
    for page in PdfReader(stream).pages:
        page_text = page.extract_text() or ''
        text.append(page_text)
        length += len(page_text)
        if length >= MAX_CONTENT_CHARS:
            break
    return '\n'.join(text)


def extract_text_from_docx(stream):
    # Check the unpacked size first: a small "zip bomb" could otherwise expand to gigabytes
    with zipfile.ZipFile(stream) as archive:
        if sum(member.file_size for member in archive.infolist()) > MAX_DOCX_UNPACKED_BYTES:
            raise ContentError('unreadable_file', 'DOCX unpacks to more than the limit')
    stream.seek(0)
    return '\n'.join(paragraph.text for paragraph in Document(stream).paragraphs)


def extract_text_from_txt(stream):
    return stream.read().decode('utf-8', errors='replace')


EXTRACTORS = {'pdf': extract_text_from_pdf, 'docx': extract_text_from_docx, 'txt': extract_text_from_txt}


def extract_text_from_upload(data, filename):
    """Text of an uploaded file (its bytes and name), cut at MAX_CONTENT_CHARS."""
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if extension not in EXTRACTORS:
        raise ContentError('unsupported_file', extension)
    try:
        text = EXTRACTORS[extension](io.BytesIO(data))
    except ContentError:
        raise
    except Exception as e:  # Damaged, encrypted or disguised files fail in many different ways
        raise ContentError('unreadable_file', f'{type(e).__name__}: {e}') from e
    return text.strip()[:MAX_CONTENT_CHARS]


def check_public_address(url):
    """Refuse URLs that aren't http(s) or whose host resolves to a non-public address (this server, the local
    network, cloud metadata services…), so a player can't make the server fetch internal pages."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ContentError('blocked_url', url)
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, ValueError) as e:
        raise ContentError('fetch_failed', f'{parsed.hostname}: {e}') from e
    for address in addresses:
        ip = ipaddress.ip_address(address.split('%')[0])  # Drop an IPv6 zone like "%eth0"
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ContentError('blocked_url', f'{parsed.hostname} -> {ip}')


def fetch_page(url):
    """The page's bytes, following at most MAX_REDIRECTS redirects and checking every address on the way."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    for _ in range(MAX_REDIRECTS + 1):
        check_public_address(url)
        response = requests.get(url, headers=headers, timeout=(5, 10), stream=True, allow_redirects=False)
        if not response.is_redirect:
            break
        response.close()
        url = urljoin(url, response.headers['Location'])
    else:
        raise ContentError('fetch_failed', 'too many redirects')

    with response:
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '')
        if content_type and not content_type.startswith(('text/', 'application/xhtml')):
            raise ContentError('fetch_failed', f'not a web page ({content_type})')
        body = bytearray()
        deadline = time.monotonic() + FETCH_DEADLINE
        for chunk in response.iter_content(64 * 1024):
            body += chunk
            if len(body) >= MAX_PAGE_BYTES:
                break  # Long enough for a quiz; the rest is ignored
            if time.monotonic() > deadline:
                raise ContentError('fetch_failed', 'download took too long')
    return bytes(body)


def extract_text_from_url(url):
    """Readable text of a public webpage, cut at MAX_CONTENT_CHARS."""
    if not urlparse(url).scheme:
        url = 'https://' + url
    try:
        html = fetch_page(url)
    except requests.exceptions.RequestException as e:
        raise ContentError('fetch_failed', f'{type(e).__name__}: {e}') from e

    soup = BeautifulSoup(html, 'html.parser')
    # Drop scripts, styles and page furniture
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    # Clean up whitespace
    lines = (line.strip() for line in soup.get_text().splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\n'.join(chunk for chunk in chunks if chunk)
    return text.strip()[:MAX_CONTENT_CHARS]


def validate_content_length(content, min_length=MIN_CONTENT_CHARS):
    """Refuse content too short to make a quiz from (longer content is already cut to MAX_CONTENT_CHARS)."""
    if len(content) < min_length:
        raise ContentError('too_short', f'{len(content)} chars')
