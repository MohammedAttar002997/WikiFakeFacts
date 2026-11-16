"""Show what was faked: compare a round's fake statement with the source sentence it was made from, word by word,
so the answer review can highlight the detail that was changed ("1 million" for "4.1 million").

Pure rules, no Flask. The AI is told to change one detail of one sentence, and the sentence it used is checked to
really be in the article (data.verified_quote), so the two usually line up word for word except for the change.
When they don't (the fake was reworded, or it's in another language than the article), compare() returns None and
the review shows the source sentence as before, without highlights.
"""
import difflib
import re

# Words (with inner dots, commas and apostrophes, so "4.1", "1,000" and "don't" stay one word) or single symbols
TOKEN = re.compile(r"\w+(?:[.,'’]\w+)*|[^\w\s]")
MIN_SHARED = 0.6   # Share of the fake's words that must match the source sentence for the comparison to make sense
MAX_CHANGES = 3    # More separate changes than this means a rewrite, not one changed detail
MAX_LEFT_OUT = 2   # Words the fake leaves out that count as the change ("not"); longer gaps just shorten the sentence
MAX_CHANGE_WORDS = 4  # A changed detail is a few words; a longer swap means the sentence was reworded


def spans(text):
    return [(m.start(), m.end()) for m in TOKEN.finditer(text)]


def compare(fake, source):
    """{'fake': segments, 'source': segments, 'changes': [(was, became)]} or None.

    segments: [(text, changed)] that join back into the original string, with the changed words marked.
    changes: the source's words and what the fake made of them ('' when the fake left words out or added some).
    """
    if not fake or not source:
        return None
    fake_spans, source_spans = spans(fake), spans(source)
    fake_words = [fake[a:b].casefold() for a, b in fake_spans]
    source_words = [source[a:b].casefold() for a, b in source_spans]
    if not fake_words:
        return None
    opcodes = difflib.SequenceMatcher(None, source_words, fake_words, autojunk=False).get_opcodes()
    shared = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag == 'equal')
    if shared / len(fake_words) < MIN_SHARED:
        return None

    marked_fake, marked_source, changes = set(), set(), []
    for n, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == 'equal':
            continue
        # Words of the source sentence before or after the part the fake was made from aren't a change
        interior = 0 < n < len(opcodes) - 1
        if tag == 'delete' and (not interior or i2 - i1 > MAX_LEFT_OUT):
            continue
        if not any(w.isalnum() or w[0].isalnum() for w in source_words[i1:i2] + fake_words[j1:j2]):
            continue  # Only punctuation differs
        if max(words(source_words[i1:i2]), words(fake_words[j1:j2])) > MAX_CHANGE_WORDS:
            return None
        marked_source.update(range(i1, i2))
        marked_fake.update(range(j1, j2))
        changes.append((text_of(source, source_spans, i1, i2), text_of(fake, fake_spans, j1, j2)))
    if not changes or len(changes) > MAX_CHANGES:
        return None
    return {'fake': segments(fake, fake_spans, marked_fake), 'source': segments(source, source_spans, marked_source),
            'changes': changes}


def words(tokens):
    return sum(1 for t in tokens if t[0].isalnum())


def text_of(text, token_spans, start, end):
    return text[token_spans[start][0]:token_spans[end - 1][1]] if start < end else ''


def segments(text, token_spans, marked):
    """[(text, changed)] covering all of `text`; neighbouring changed words (and the spaces between them) form one
    segment, so "1 million" is highlighted as one piece."""
    out, position = [], 0
    for index, (start, end) in enumerate(token_spans):
        changed = index in marked
        gap = text[position:start]
        if changed and out and out[-1][1] and not gap.strip():
            out[-1] = (out[-1][0] + gap + text[start:end], True)
        else:
            if gap:
                out.append((gap, False))
            out.append((text[start:end], changed))
        position = end
    if position < len(text):
        out.append((text[position:], False))
    # Join neighbouring unchanged pieces back together
    merged = []
    for piece, changed in out:
        if merged and not changed and not merged[-1][1]:
            merged[-1] = (merged[-1][0] + piece, False)
        else:
            merged.append((piece, changed))
    return merged
