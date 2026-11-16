"""Show what was faked (fake_diff.py): the changed detail is found and marked, and rewrites aren't forced into a diff."""
import pytest

from fake_diff import compare


def joined(segments):
    return ''.join(text for text, _ in segments)


def marked(segments):
    return [text for text, changed in segments if changed]


def test_a_changed_number_is_marked_on_both_sides():
    source = "The black hole at the Milky Way's center has about 4.1 million solar masses."
    fake = "The black hole at the Milky Way's center has about 1 million solar masses."
    diff = compare(fake, source)
    assert diff['changes'] == [('4.1', '1')]
    assert marked(diff['fake']) == ['1'] and marked(diff['source']) == ['4.1']
    assert joined(diff['fake']) == fake and joined(diff['source']) == source


def test_neighbouring_changed_words_are_one_highlight():
    diff = compare('Paris is the capital of Spain and Portugal.', 'Paris is the capital of France.')
    assert diff['changes'] == [('France', 'Spain and Portugal')]
    assert marked(diff['fake']) == ['Spain and Portugal']


def test_the_fake_is_usually_a_shorter_part_of_a_longer_sentence():
    source = 'Founded in 1889, the company, which later moved to Kyoto, first made playing cards.'
    fake = 'The company first made video games.'
    diff = compare(fake, source)
    # Clauses the fake leaves out just shorten the sentence; the swapped words are the change
    assert diff['changes'] == [('playing cards', 'video games')]
    assert marked(diff['source']) == ['playing cards']
    assert joined(diff['source']) == source


def test_a_left_out_word_inside_the_sentence_is_a_change():
    diff = compare('The treaty was signed by Spain.', 'The treaty was not signed by Spain.')
    assert diff['changes'] == [('not', '')]
    assert marked(diff['source']) == ['not'] and marked(diff['fake']) == []


def test_an_added_word_is_a_change():
    diff = compare('The river never freezes in winter.', 'The river freezes in winter.')
    assert diff['changes'] == [('', 'never')] and marked(diff['fake']) == ['never']


def test_case_and_number_formats_count_as_words():
    diff = compare("It's 1,200 km long.", "it's 1,500 km long.")
    assert diff['changes'] == [('1,500', '1,200')]


def test_punctuation_alone_is_not_a_change():
    diff = compare('In 1990, the bridge opened to 5 lanes.', 'In 1990 the bridge opened to 4 lanes.')
    assert diff['changes'] == [('4', '5')]


@pytest.mark.parametrize('fake, source', [
    # A real AI round: a clause reworded, not one detail changed
    ('ICs have three main advantages including size, cost, and weight.',
     'ICs have three main advantages over circuits constructed out of discrete components: size, cost, and performance.'),
    ('Die Sonne ist ein Stern.', 'The Sun is a star at the centre of the Solar System.'),   # Another language
    ('Bees mostly eat nectar and pollen.', 'Honey bees feed on flowers for energy.'),       # Reworded
    ('A B C D E F', 'A x C y E z G'),                                                       # Too many changes
    ('Same sentence.', 'Same sentence.'),                                                   # Nothing changed
    ('', 'Anything.'),
    ('Something.', None),
])
def test_no_diff_when_the_two_dont_line_up(fake, source):
    assert compare(fake, source) is None


# ---------- In the answer review ----------------------------------------------------------------------------------

def test_the_review_page_highlights_the_change(login, app_ctx):
    import json
    from app import db
    from models import QuizSession, User
    client = login('Ann')
    facts = {'The tower is 330 metres tall.': False, 'It is in Paris.': True, 'It opened in 1889.': True,
             'It is made of iron.': True}
    quiz = QuizSession(user_id=User.query.filter_by(username='Ann').one().id, topic='Eiffel Tower',
                       questions_json=json.dumps([facts]), answers_json=json.dumps(['It is in Paris.']),
                       quotes_json=json.dumps(['The tower is 300 metres tall.']), total_questions=1,
                       current_question_index=1, is_completed=True, source_url='https://en.wikipedia.org/wiki/Eiffel_Tower')
    db.session.add(quiz)
    db.session.commit()
    html = client.get(f'/review/{quiz.id}').get_data(as_text=True)
    assert 'What the fake changed:' in html
    assert '<mark class="diff-fake">330</mark>' in html and '<mark class="diff-source">300</mark>' in html


def test_rounds_without_a_matching_source_show_it_plainly(login, app_ctx):
    from conftest import answers, play_quiz
    html = play_quiz(login('Ann'), answers(1, 3))  # The stub's quotes don't line up with its fakes
    assert 'Quote proving round 0.' in html and 'What the fake changed:' not in html
