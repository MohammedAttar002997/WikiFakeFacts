"""UI translations.

Strings are written in English in the code and looked up in locales/<lang>.json, which map the English text to
its translation. Anything missing falls back to English, so a new string shows up in English until translated.

- Templates: {{ _('Play now') }}, {{ _('Ready, {name}?').format(name=user) }} (arguments are HTML-escaped),
  {{ _n('{n} game', '{n} games', count) }} (only {n} is filled in, so keep other values in separate strings),
  and the |date / |datetime filters.
- Catalog text is inserted as-is, so it must not contain HTML: no ", <, > or &entities; (use typographic
  quotes like “…” or «…»). tests/test_i18n.py checks this.
- Python: translate(text) or translate_plural(...) for the current request's language.

Run `python translations.py` to list strings that are missing from a catalog.
"""
import json
import os
import re

from markupsafe import Markup

LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locales')
UI_LANGUAGES = ['en', 'es', 'fr', 'de', 'it', 'pt', 'ar']
RTL_LANGUAGES = {'ar'}  # Written right to left: pages get dir="rtl" and Bootstrap's RTL stylesheet
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _load_catalogs():
    catalogs = {}
    for lang in UI_LANGUAGES[1:]:
        path = os.path.join(LOCALES_DIR, f'{lang}.json')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                catalogs[lang] = json.load(f)
    return catalogs


CATALOGS = _load_catalogs()


def current_language():
    from flask import has_request_context, session
    lang = session.get('language', 'en') if has_request_context() else 'en'
    return lang if lang in UI_LANGUAGES else 'en'


def translate(text, lang=None):
    lang = lang or current_language()
    return CATALOGS.get(lang, {}).get(text) or text


def translate_plural(singular, plural, n, lang=None):
    """Translate and fill {n}; plural forms are picked by English rules (1 vs. everything else)."""
    return translate(singular if n == 1 else plural, lang).format(n=n)


def format_date(value, with_time=False, lang=None):
    """'Oct 21, 2026' in English, '21 Oct 2026' style elsewhere, with translated month names."""
    if not value:
        return ''
    lang = lang or current_language()
    month = translate(MONTHS[value.month - 1], lang)
    text = f'{month} {value.day:02d}, {value.year}' if lang == 'en' else f'{value.day} {month} {value.year}'
    return f'{text} · {value:%H:%M}' if with_time else text


def template_globals():
    """_() and _n() for Jinja. They're globals (not context variables) so imported macros can use them too.
    Catalog text is trusted, so it's returned as Markup; .format() on Markup escapes its arguments."""
    return {
        '_': lambda text: Markup(translate(text)),
        '_n': lambda singular, plural, n: Markup(translate(singular if n == 1 else plural)).format(n=n),
    }


def template_context():
    lang = current_language()
    return {'ui_language': lang, 'ui_languages': UI_LANGUAGES,
            'ui_direction': 'rtl' if lang in RTL_LANGUAGES else 'ltr'}


def dynamic_strings():
    """Strings that reach _() through variables rather than literals."""
    from default_text import CATEGORIES, DIFFICULTY_OPTIONS, GAME_LENGTH_OPTIONS, LEADERBOARD_PERIODS
    from badges import BADGES
    strings = set(MONTHS) | set(CATEGORIES.values()) | set(LEADERBOARD_PERIODS.values())
    for label in list(DIFFICULTY_OPTIONS.values()) + list(GAME_LENGTH_OPTIONS.values()):
        # "Easy (Ages 7-12)" is shown as "Easy" and "Ages 7-12"
        name, _, detail = label.partition(' (')
        strings |= {name, detail.rstrip(')')}
    for badge in BADGES:
        strings |= {badge['name'], badge['description']}
    return strings


# _('…'), _n('…', '…'), translate('…') and translate_plural('…', '…') with single- or double-quoted literals
LITERAL = r"""(?P<q{i}>['"])(?P<s{i}>(?:\\.|(?!(?P=q{i})).)*)(?P=q{i})"""
CALL_PATTERN = re.compile(r"\b(?:_n?|translate(?:_plural)?)\(\s*" + LITERAL.format(i=1) + r"(?:\s*,\s*" + LITERAL.format(i=2) + r")?")


def find_missing():
    """Strings used in the code (templates, .py, .js) that a catalog doesn't translate yet."""
    root = os.path.dirname(os.path.abspath(__file__))
    used = dynamic_strings()
    for folder, ext in (('templates', '.html'), ('.', '.py'), ('static/js', '.js')):
        for name in os.listdir(os.path.join(root, folder)):
            if not name.endswith(ext) or name == 'translations.py':
                continue
            with open(os.path.join(root, folder, name), encoding='utf-8') as f:
                source = f.read()
            for m in CALL_PATTERN.finditer(source):
                for group in ('s1', 's2'):
                    if m.group(group):
                        used.add(m.group(group).replace("\\'", "'").replace('\\"', '"'))
    return {lang: sorted(used - set(CATALOGS.get(lang, {}))) for lang in UI_LANGUAGES[1:]}, sorted(used)


if __name__ == '__main__':
    missing, used = find_missing()
    print(f'{len(used)} strings in use')
    for lang, strings in missing.items():
        print(f'{lang}: {len(strings)} missing')
