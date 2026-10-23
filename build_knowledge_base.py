"""Save a copy of every built-in topic's Wikipedia article to knowledge_base.json, for when Wikipedia is down.

    python build_knowledge_base.py            # fetch the topics that aren't saved yet
    python build_knowledge_base.py --all      # fetch every topic again

The file is written after each article, so an interrupted run picks up where it stopped. Run it after changing the
topics in default_text.MENU, then rebuild the search index with rag_indexer.py.
"""
import json
import os
import re
import sys
import time

import requests

from default_text import MENU

KB_PATH = os.path.join(os.path.dirname(__file__), 'knowledge_base.json')
API_URL = 'https://en.wikipedia.org/w/api.php'
# Wikimedia asks for a descriptive User-Agent and throttles generic ones
HEADERS = {'User-Agent': 'WikiFakeFact/1.0 (educational quiz game; knowledge-base snapshot)'}
PAUSE_SECONDS = 2  # Between articles, to stay well under Wikimedia's rate limits
ATTEMPTS = 5
MAX_ARTICLE_CHARS = 15_000  # The start of an article has the best-known facts; more only adds size and AI cost
# Sections after these are lists of links and sources, not facts to quiz on
END_SECTIONS = re.compile(r'^== (See also|References|Notes|Further reading|External links|Bibliography|Sources)'
                          r' ==\s*$', re.M)


def fetch_article(topic):
    """The article's plain text, in one request, waiting and retrying when Wikimedia throttles."""
    params = {'action': 'query', 'prop': 'extracts', 'explaintext': 1, 'titles': topic, 'redirects': 1,
              'format': 'json', 'formatversion': 2}
    for attempt in range(ATTEMPTS):
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
        if response.status_code in (429, 503) and attempt < ATTEMPTS - 1:
            wait = int(response.headers.get('Retry-After') or 0) or 15 * (attempt + 1)
            print(f'  throttled, waiting {wait}s', flush=True)
            time.sleep(wait)
            continue
        response.raise_for_status()
        text = response.json()['query']['pages'][0].get('extract') or ''
        if not text:
            raise ValueError(f'No article called "{topic}"')
        return text


def trim_article(text):
    """The article's text up to its link sections, cut at a paragraph end within MAX_ARTICLE_CHARS."""
    match = END_SECTIONS.search(text)
    text = (text[:match.start()] if match else text).strip()
    if len(text) <= MAX_ARTICLE_CHARS:
        return text
    cut = text.rfind('\n', 0, MAX_ARTICLE_CHARS)
    return text[:cut if cut > MAX_ARTICLE_CHARS // 2 else MAX_ARTICLE_CHARS].strip()


def main():
    topics = [topic for category in MENU.values() for topic in category]
    saved = {}
    if '--all' not in sys.argv[1:] and os.path.exists(KB_PATH):
        with open(KB_PATH, encoding='utf-8') as f:
            saved = {topic: text for topic, text in json.load(f).items() if topic in topics}
    failed = []
    for number, topic in enumerate(topics, 1):
        if topic in saved:
            continue
        try:
            saved[topic] = trim_article(fetch_article(topic))
            print(f'[{number}/{len(topics)}] {topic}: {len(saved[topic]):,} characters', flush=True)
        except Exception as e:
            failed.append(topic)
            print(f'[{number}/{len(topics)}] {topic}: FAILED ({e!r})', flush=True)
            continue
        # Menu order, and only current topics (an older file may have others)
        with open(KB_PATH, 'w', encoding='utf-8') as f:
            json.dump({t: saved[t] for t in topics if t in saved}, f, ensure_ascii=False, indent=1)
        time.sleep(PAUSE_SECONDS)
    if failed:
        sys.exit(f'{len(failed)} topics failed; run it again to retry them: {", ".join(failed)}')
    print(f'All {len(topics)} articles are saved in knowledge_base.json.')


if __name__ == '__main__':
    main()
