"""Player-written fakes: write one from a round you played, the AI check, playing, the feed, reports and moderation."""
import json
from types import SimpleNamespace

import pytest

import app as app_module
import player_fakes as rules
from app import db
from conftest import answers, play_quiz
from hidden_rounds import fingerprint
from models import DailyChallenge, HiddenRound, PlayerFake, PlayerFakePlay, QuizSession, User

GOOD_FAKE = 'The Eiffel Tower was painted bright green in 1950.'


# ---------- Rules (player_fakes.py) -------------------------------------------------------------------------------

@pytest.mark.parametrize('fake, problem', [
    ('Too short.', 'too_short'),
    ('x' * 201, 'too_long'),
    ('It opened in 1889', 'same_as_real'),
    ('See www.example.com for the truth here.', 'link'),
    (GOOD_FAKE, None),
])
def test_problems_caught_before_the_ai(fake, problem):
    assert rules.problem(fake, ['It opened in 1889.', 'It is in Paris.', 'It is made of iron.']) == problem


def test_clean_squashes_spaces():
    assert rules.clean('  The   tower\nis green. ') == 'The tower is green.'


def test_options_are_shuffled_the_same_way_for_everyone():
    facts = ['A fact one.', 'B fact two.', 'C fact three.']
    first = rules.options(facts, 'Fake one.', 'code1')
    assert first == rules.options(facts, 'Fake one.', 'code1') and sorted(first) == sorted(facts + ['Fake one.'])


@pytest.mark.parametrize('changes, result', [
    ({}, 'ok'),
    ({'statement_is_true': True}, 'true'),
    ({'on_topic': False}, 'off_topic'),
    ({'believable': False}, 'implausible'),
    ({'appropriate': False}, 'inappropriate'),
    ({'appropriate': False, 'statement_is_true': True, 'on_topic': False}, 'inappropriate'),  # Most serious first
    ({'statement_is_true': True, 'believable': False}, 'true'),
])
def test_verdict_from_the_ai_answers(changes, result):
    assert rules.verdict(ai_answers(**changes)) == result


def test_a_false_believable_on_topic_fake_is_accepted():
    """The case a player hit: a wrong formula is a fine fake. The old single-label check turned it down while its
    own reason said it was false."""
    check = ai_answers(analysis='Cyclic amines do not have the general formula RA2N, so the statement is false.')
    assert rules.verdict(check) == 'ok'


def test_trickiest_needs_enough_plays():
    # (plays, fooled, id): 2 of 2 fooled isn't enough plays; 3 of 4 beats 1 of 3; newest first among the rest
    fakes = [(2, 2, 1), (4, 3, 2), (3, 1, 3), (0, 0, 4)]
    ranked = sorted(fakes, key=lambda f: rules.trickiest_key(*f))
    assert [f[2] for f in ranked] == [2, 3, 4, 1]


# ---------- Writing one -------------------------------------------------------------------------------------------

def ai_answers(**changes):
    """The AI check's yes/no answers (data.FakeCheck) for a good fake, with some changed."""
    return SimpleNamespace(**{'analysis': 'Thinking.', 'statement_is_true': False, 'on_topic': True, 'believable': True,
                              'appropriate': True, 'correction': 'It was never green.', **changes})


class CheckStub:
    def __init__(self):
        self.calls, self.changes, self.error = [], {}, None

    def __call__(self, fake, real_facts, topic, content, language='English'):
        self.calls.append({'fake': fake, 'real_facts': real_facts, 'topic': topic, 'language': language})
        if self.error:
            raise self.error
        return ai_answers(**self.changes)


@pytest.fixture
def check(monkeypatch):
    stub = CheckStub()
    monkeypatch.setattr(app_module, 'check_player_fake', stub)
    monkeypatch.setattr(app_module, 'article_text', lambda title: 'Article text.')
    return stub


@pytest.fixture
def played(login, app_ctx):
    """Ann finished a 3-round quiz (from Wikipedia): (client, quiz)."""
    client = login('Ann')
    play_quiz(client, answers(1, 3))
    return client, QuizSession.query.one()


def write(client, quiz, round_index=0, fake=GOOD_FAKE):
    return client.post('/fakes/new', data={'quiz': quiz.id, 'round': round_index, 'fake': fake})


def test_the_review_offers_writing_a_fake_per_round(played):
    client, quiz = played
    assert client.get('/results').get_data(as_text=True).count('Write your own fake for this round') == 3


def test_writing_a_fake_shows_the_rounds_true_facts(played):
    client, quiz = played
    html = client.get(f'/fakes/new?quiz={quiz.id}&round=1').get_data(as_text=True)
    assert 'R1 true A' in html and 'R1 true B' in html and 'R1 FAKE' in html  # The AI's fake, struck through


def test_a_checked_fake_is_saved_and_shareable(played, check):
    client, quiz = played
    response = write(client, quiz, 1)
    fake = PlayerFake.query.one()
    assert response.headers['Location'].endswith(f'/f/{fake.code}')
    assert fake.fake == GOOD_FAKE and json.loads(fake.real_facts_json) == ['R1 true A', 'R1 true B', 'R1 true C']
    assert fake.correction == 'It was never green.' and fake.topic == quiz.topic and fake.source_url == quiz.source_url
    assert check.calls[0]['real_facts'] == ['R1 true A', 'R1 true B', 'R1 true C']
    html = client.get(f'/f/{fake.code}').get_data(as_text=True)
    assert 'Share this fake' in html and GOOD_FAKE in html


@pytest.mark.parametrize('changes, message', [
    ({'statement_is_true': True}, 'actually true'),
    ({'on_topic': False}, 'not about the same topic'),
    ({'believable': False}, 'too easy to spot'),
    ({'appropriate': False}, 'Keep it friendly'),
])
def test_the_ai_can_turn_a_fake_down(played, check, changes, message):
    client, quiz = played
    check.changes = changes
    html = write(client, quiz).get_data(as_text=True)
    assert message in html and PlayerFake.query.count() == 0


def test_obvious_problems_dont_reach_the_ai(played, check):
    client, quiz = played
    assert 'too short' in write(client, quiz, fake='Short.').get_data(as_text=True)
    assert 'cannot contain links' in write(client, quiz, fake='Read www.example.com for the real story.').get_data(as_text=True)
    assert check.calls == [] and PlayerFake.query.count() == 0


def test_a_failing_check_shares_nothing(played, check):
    client, quiz = played
    check.error = RuntimeError('OpenAI is down')
    assert "couldn&#39;t check your fake" in write(client, quiz).get_data(as_text=True)
    assert PlayerFake.query.count() == 0


def test_only_your_own_finished_quizzes(played, check, login):
    _, quiz = played
    assert write(login('Ben'), quiz).headers['Location'].endswith('/fakes')
    assert PlayerFake.query.count() == 0


def test_not_from_todays_daily_challenge(login, check, app_ctx):
    """Its true facts would spoil today's challenge for everyone who hasn't played it."""
    facts = {'A is true.': True, 'B is true.': True, 'C is true.': True, 'D is the fake.': False}
    daily = DailyChallenge(date=app_module.today_utc(), language='English', topic='Cat',
                           questions_json=json.dumps([facts]), explanations_json='["Why."]',
                           source_url='https://en.wikipedia.org/wiki/Cat')
    db.session.add(daily)
    db.session.commit()
    client = login('Ann')
    client.post('/daily/start')
    client.post('/quiz', data={'option': 'D is the fake.'})
    quiz = QuizSession.query.one()
    assert 'Write your own fake' not in client.get('/results').get_data(as_text=True)
    assert write(client, quiz).headers['Location'].endswith('/fakes') and check.calls == []


def test_not_from_a_hidden_round(played, check):
    client, quiz = played
    statements = json.loads(quiz.questions_json)[0]
    db.session.add(HiddenRound(fingerprint=fingerprint(statements), topic='x', statements_json=json.dumps(statements)))
    db.session.commit()
    assert write(client, quiz, 0).headers['Location'].endswith('/fakes')


def test_writing_is_rate_limited(played, check, monkeypatch):
    client, quiz = played
    monkeypatch.setitem(app_module.RATE_LIMITS, 'fake', (1, app_module.timedelta(hours=1)))
    write(client, quiz, 0)
    assert 'a bit fast' in write(client, quiz, 1).get_data(as_text=True)
    assert PlayerFake.query.count() == 1


# ---------- Playing one -------------------------------------------------------------------------------------------

@pytest.fixture
def fake(played, check):
    client, quiz = played
    write(client, quiz, 1)
    return PlayerFake.query.one()


def answer(client, fake, pick):
    return client.post(f'/f/{fake.code}', data={'option': pick})


def test_a_player_spots_it_and_it_counts_once(fake, login):
    ben = login('Ben')
    answer(ben, fake, GOOD_FAKE)
    answer(ben, fake, 'R1 true A')  # A second answer is ignored
    db.session.refresh(fake)
    assert (fake.plays, fake.fooled) == (1, 0)
    assert 'You spotted it!' in ben.get(f'/f/{fake.code}').get_data(as_text=True)


def test_a_fooled_player_sees_who_fooled_them(fake, login):
    ben = login('Ben')
    answer(ben, fake, 'R1 true B')
    db.session.refresh(fake)
    assert (fake.plays, fake.fooled) == (1, 1)
    html = ben.get(f'/f/{fake.code}').get_data(as_text=True)
    assert 'Ann fooled you!' in html and 'It was never green.' in html and 'fooled 1 of 1 player (100%)' in html


def test_guests_can_play_from_the_link_once_per_session(fake, app):
    guest = app.test_client()
    assert 'Spot the Fake Fact!' in guest.get(f'/f/{fake.code}').get_data(as_text=True)
    answer(guest, fake, 'R1 true C')
    answer(guest, fake, GOOD_FAKE)
    db.session.refresh(fake)
    assert (fake.plays, fake.fooled) == (1, 1) and PlayerFakePlay.query.count() == 0
    assert 'Ann fooled you!' in guest.get(f'/f/{fake.code}').get_data(as_text=True)


def test_the_author_cant_play_their_own(fake, played):
    client, _ = played
    answer(client, fake, GOOD_FAKE)
    db.session.refresh(fake)
    assert fake.plays == 0


def test_made_up_answers_are_ignored(fake, login):
    answer(login('Ben'), fake, 'Something not on the card.')
    db.session.refresh(fake)
    assert fake.plays == 0


# ---------- The feed ----------------------------------------------------------------------------------------------

def test_the_feed_lists_public_fakes(fake, login, app):
    html = app.test_client().get('/fakes').get_data(as_text=True)
    assert f'/f/{fake.code}' in html and 'by Ann' in html and 'No one has played it yet' in html
    ben = login('Ben')
    answer(ben, fake, 'R1 true A')
    html = ben.get('/fakes').get_data(as_text=True)
    assert 'fooled 1 of 1 player' in html and '>Played<' in html.replace('</i>', '')


def test_the_trickiest_tab_works(fake, app):
    assert f'/f/{fake.code}' in app.test_client().get('/fakes?sort=tricky').get_data(as_text=True)


def test_the_feed_is_a_challenges_tab(app):
    html = app.test_client().get('/fakes').get_data(as_text=True)
    assert 'href="/daily"' in html and 'href="/event"' in html and 'No player fakes yet' in html


# ---------- Reports and moderation --------------------------------------------------------------------------------

@pytest.fixture
def admins(app, monkeypatch):
    monkeypatch.setitem(app.config, 'ADMIN_USERS', 'Boss')


def test_players_who_answered_can_report_once(fake, login):
    ben = login('Ben')
    ben.post(f'/f/{fake.code}/report')  # Not answered yet: nothing
    db.session.refresh(fake)
    assert fake.reports == 0
    answer(ben, fake, GOOD_FAKE)
    ben.post(f'/f/{fake.code}/report')
    ben.post(f'/f/{fake.code}/report')
    db.session.refresh(fake)
    assert fake.reports == 1


def test_admin_hides_a_reported_fake(fake, login, admins, app):
    ben = login('Ben')
    answer(ben, fake, GOOD_FAKE)
    ben.post(f'/f/{fake.code}/report')
    boss = login('Boss')
    assert 'Reported player fakes' in boss.get('/admin').get_data(as_text=True)
    assert GOOD_FAKE in boss.get('/admin').get_data(as_text=True)
    boss.post(f'/admin/fakes/{fake.id}/hide')
    assert app.test_client().get(f'/f/{fake.code}').status_code == 404
    assert f'/f/{fake.code}' not in app.test_client().get('/fakes').get_data(as_text=True)
    boss.post(f'/admin/fakes/{fake.id}/show')
    db.session.refresh(fake)
    assert fake.status is None and fake.reports == 0


def test_only_admins_moderate(fake, login, admins):
    assert login('Ben').post(f'/admin/fakes/{fake.id}/hide').status_code == 404


def test_deleting_a_player_deletes_their_fakes_and_plays(fake, login):
    answer(login('Ben'), fake, GOOD_FAKE)
    app_module.delete_player_data(User.query.filter_by(username='Ann').one())
    assert PlayerFake.query.count() == 0 and PlayerFakePlay.query.count() == 0
