"""Try the AI check for player-written fakes (data.check_player_fake) on a few examples, with your own OpenAI key.

    python scripts/try_fake_check.py                       # the built-in examples about the Eiffel Tower
    python scripts/try_fake_check.py "Your fake here."     # one fake of your own about the same topic

Each check is one small call to the check model (a fraction of a cent). Nothing is saved.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # data.py loads .env from here

import data  # noqa: E402
from player_fakes import verdict  # noqa: E402

TOPIC = 'Eiffel Tower'
REAL = ['The Eiffel Tower is in Paris.', 'It was designed by the company of Gustave Eiffel.', 'It was completed in 1889.']
EXAMPLES = [
    ('ok', 'The Eiffel Tower was originally painted bright green.'),
    ('ok', 'The Eiffel Tower was completed in 1925.'),
    ('true', 'The Eiffel Tower is made of wrought iron.'),
    ('implausible', 'The Eiffel Tower is secretly a giant robot that walks at night.'),
    ('off_topic', 'Bananas are a good source of potassium.'),
]

if __name__ == '__main__':
    fakes = [('?', ' '.join(sys.argv[1:]))] if len(sys.argv) > 1 else EXAMPLES
    content = data.article_text(TOPIC)
    print(f'Article: {len(content)} characters' if content else 'Article: not available (checking without it)')
    for expected, fake in fakes:
        result = data.check_player_fake(fake, REAL, TOPIC, content, 'English')
        print(f'\n{fake}\n  verdict: {verdict(result)} (expected {expected})\n  analysis: {result.analysis}\n'
              f'  correction: {result.correction}')
