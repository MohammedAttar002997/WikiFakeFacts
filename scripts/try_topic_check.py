"""Try the AI age check for typed topics (data.check_topic_audience) with your own OpenAI key.

    python scripts/try_topic_check.py                    # a few built-in examples
    python scripts/try_topic_check.py "Some article"     # one Wikipedia article title of your own

Each check is one small call to the check model. Nothing is saved.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # data.py loads .env from here

import any_topic  # noqa: E402
import data  # noqa: E402

EXAMPLES = [('children', 'Black hole'), ('children', 'World War II'), ('teens', 'Cannabis (drug)'),
            ('adults', 'Pornography'), ('children', 'Taylor Swift')]

if __name__ == '__main__':
    topics = [('?', ' '.join(sys.argv[1:]))] if len(sys.argv) > 1 else EXAMPLES
    for expected, title in topics:
        audience = data.check_topic_audience(title)
        allowed = [d.split(' (')[0] for d in any_topic.ALLOWED_UP_TO if any_topic.allowed(audience, d)]
        print(f'{title}: {audience} (expected {expected}); playable at: {", ".join(allowed) or "nothing"}')
