"""Player-written fakes (rules in player_fakes.py): write one from a round you played (/fakes/new), play one from
its link (/f/<code>) or the community feed (/fakes), report one, and hide one from /admin. Routes register on the
app when app.py imports this."""
import json
import secrets
from urllib.parse import unquote, urlparse

from flask import abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

import app as core
import hidden_rounds
import player_fakes as rules
from app import app, current_user, db, hidden_fingerprints, over_limit, save_error, today_utc
from default_text import LANGUAGE_OPTIONS
from models import DailyChallenge, PlayerFake, PlayerFakePlay, QuizSession, User
from translations import translate as _
from views.admin import is_admin

# Session key: [code, index of the statement picked] for fakes a signed-out visitor answered, so a reload doesn't
# count them twice. Indexes, not the statements, keep the session cookie small
GUEST_ANSWERS = 'fake_answers'
GUEST_KEPT = 30

PROBLEMS = {
    'too_short': lambda: _('Your fake is too short. Write a full sentence.'),
    'too_long': lambda: _('Your fake is too long. Keep it under 200 characters.'),
    'same_as_real': lambda: _("That's one of the true facts. Change something to make it fake."),
    'link': lambda: _('Fakes cannot contain links.'),
}


def article_title(source_url):
    """'Eiffel Tower' from https://en.wikipedia.org/wiki/Eiffel_Tower."""
    return unquote(urlparse(source_url).path.rsplit('/', 1)[-1]).replace('_', ' ')


def can_write_fake(quiz_session):
    """Whether a finished quiz's rounds can be turned into player fakes: it has a Wikipedia article to check fakes
    against, and it isn't today's daily challenge (whose true facts would spoil it for everyone else)."""
    if not quiz_session or not quiz_session.is_completed or not quiz_session.source_url:
        return False
    if quiz_session.daily_challenge_id:
        daily = db.session.get(DailyChallenge, quiz_session.daily_challenge_id)
        return bool(daily) and daily.date != today_utc()
    return True


app.jinja_env.globals['can_write_fake'] = can_write_fake


def source_round(user, quiz_id, index):
    """(quiz, round statements) the signed-in player may write a fake for, or (None, None)."""
    quiz = db.session.get(QuizSession, quiz_id) if quiz_id is not None else None
    if not quiz or quiz.user_id != user.id or not can_write_fake(quiz) or index is None:
        return None, None
    rounds = json.loads(quiz.questions_json)
    if not 0 <= index < min(len(rounds), quiz.current_question_index):
        return None, None
    statements = rounds[index]
    if hidden_rounds.fingerprint(statements) in hidden_fingerprints():
        return None, None
    return quiz, statements


@app.route('/fakes/new', methods=['GET', 'POST'])
def fake_new():
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    quiz, statements = source_round(user, request.values.get('quiz', type=int), request.values.get('round', type=int))
    if not quiz:
        flash(_('Pick a round from one of your finished quizzes to write a fake for it.'), 'info')
        return redirect(url_for('fakes_feed'))
    real_facts = [s for s, real in statements.items() if real]
    ai_fake = next((s for s, real in statements.items() if not real), '')
    fake, problem = '', None
    if request.method == 'POST':
        fake = rules.clean(request.form.get('fake'))
        code = rules.problem(fake, real_facts + [ai_fake])
        if code:
            problem = PROBLEMS[code]()
        elif over_limit('fake', user.id):
            problem = _("You're going a bit fast. Please wait a few minutes and try again.")
        else:
            try:
                title = article_title(quiz.source_url)
                check = core.check_player_fake(fake, real_facts, quiz.topic, core.article_text(title),
                                               LANGUAGE_OPTIONS.get(session.get('language', 'en'), 'English'))
            except Exception as error:
                save_error(error)
                check = None
                problem = _("We couldn't check your fake right now. Please try again in a moment.")
            if check and check.verdict != 'ok':
                problem = check.reason or _("This fake can't be shared. Try changing a different detail.")
            elif check:
                made = PlayerFake(code=secrets.token_urlsafe(8), user_id=user.id, topic=quiz.topic[:100],
                                  real_facts_json=json.dumps(real_facts), fake=fake,
                                  correction=check.correction or None, source_url=quiz.source_url)
                db.session.add(made)
                db.session.commit()
                flash(_('Your fake is live! Share the link and see who it fools.'), 'success')
                return redirect(url_for('fake_play', code=made.code))
    return render_template('fake_new.html', quiz=quiz, index=request.values.get('round', type=int),
                           real_facts=real_facts, ai_fake=ai_fake, fake=fake, problem=problem,
                           max_length=rules.MAX_LENGTH)


@app.route('/fakes')
def fakes_feed():
    sort = 'tricky' if request.args.get('sort') == 'tricky' else 'new'
    page = max(1, request.args.get('page', 1, type=int))
    query = PlayerFake.query.filter(PlayerFake.status.is_(None))
    if sort == 'new':
        rows = query.order_by(PlayerFake.id.desc()).offset((page - 1) * rules.PAGE_SIZE).limit(rules.PAGE_SIZE + 1).all()
    else:
        # Ranked in Python: the share fooled isn't a column; the newest few hundred are plenty to rank
        candidates = query.order_by(PlayerFake.id.desc()).limit(500).all()
        candidates.sort(key=lambda f: rules.trickiest_key(f.plays, f.fooled, f.id))
        rows = candidates[(page - 1) * rules.PAGE_SIZE:page * rules.PAGE_SIZE + 1]
    more = len(rows) > rules.PAGE_SIZE
    rows = rows[:rules.PAGE_SIZE]
    authors = {u.id: u for u in User.query.filter(User.id.in_({f.user_id for f in rows}))} if rows else {}
    user = current_user()
    if user:
        played = {p.fake_id for p in PlayerFakePlay.query.filter(PlayerFakePlay.user_id == user.id,
                                                                 PlayerFakePlay.fake_id.in_([f.id for f in rows]))}
    else:
        codes = {code for code, _ in session.get(GUEST_ANSWERS, [])}
        played = {f.id for f in rows if f.code in codes}
    return render_template('fakes.html', fakes=rows, authors=authors, sort=sort, page=page, more=more,
                           played=played, me_id=user.id if user else None)


def answer_of(fake, user, statements):
    """(picked statement, correct?) if this visitor already answered the fake, else None."""
    if user:
        play = PlayerFakePlay.query.filter_by(fake_id=fake.id, user_id=user.id).first()
        return (play.picked, play.correct) if play else None
    for code, index in session.get(GUEST_ANSWERS, []):
        if code == fake.code and 0 <= index < len(statements):
            return statements[index], statements[index] == fake.fake
    return None


@app.route('/f/<code>', methods=['GET', 'POST'])
def fake_play(code):
    fake = PlayerFake.query.filter_by(code=code).first()
    user = current_user()
    if not fake or (fake.status == 'hidden' and not (user and (user.id == fake.user_id or is_admin(user)))):
        abort(404)
    real_facts = json.loads(fake.real_facts_json)
    statements = rules.options(real_facts, fake.fake, fake.code)
    is_author = bool(user) and user.id == fake.user_id
    answer = None if is_author else answer_of(fake, user, statements)
    if request.method == 'POST' and not is_author and answer is None:
        picked = request.form.get('option')
        if picked not in statements:
            return redirect(url_for('fake_play', code=code))
        correct = picked == fake.fake
        if user:
            db.session.add(PlayerFakePlay(fake_id=fake.id, user_id=user.id, picked=picked, correct=correct))
            try:
                db.session.flush()
            except IntegrityError:
                db.session.rollback()  # A double click: the first answer counts
                return redirect(url_for('fake_play', code=code))
        else:
            session[GUEST_ANSWERS] = (session.get(GUEST_ANSWERS, []) + [[code, statements.index(picked)]])[-GUEST_KEPT:]
        PlayerFake.query.filter_by(id=fake.id).update(
            {PlayerFake.plays: PlayerFake.plays + 1, PlayerFake.fooled: PlayerFake.fooled + (0 if correct else 1)},
            synchronize_session=False)
        db.session.commit()
        return redirect(url_for('fake_play', code=code))
    db.session.refresh(fake)
    my_play = PlayerFakePlay.query.filter_by(fake_id=fake.id, user_id=user.id).first() if user and answer else None
    return render_template('fake_play.html', fake=fake, author=db.session.get(User, fake.user_id),
                           statements=statements, answer=answer, is_author=is_author, my_play=my_play,
                           share_url=url_for('fake_play', code=code, _external=True),
                           fooled_pct=round(rules.fooled_share(fake.plays, fake.fooled) * 100))


@app.route('/f/<code>/report', methods=['POST'])
def fake_report(code):
    """A signed-in player who answered the fake says it's wrong or not OK; the owner sees it on /admin."""
    fake = PlayerFake.query.filter_by(code=code).first_or_404()
    user = current_user()
    play = PlayerFakePlay.query.filter_by(fake_id=fake.id, user_id=user.id).first() if user else None
    if play and not play.reported:
        play.reported = True
        PlayerFake.query.filter_by(id=fake.id).update({PlayerFake.reports: PlayerFake.reports + 1},
                                                      synchronize_session=False)
        db.session.commit()
        flash(_("Thanks! We'll check this fake."), 'success')
    return redirect(url_for('fake_play', code=code))


@app.route('/admin/fakes/<int:fake_id>/<action>', methods=['POST'])
def admin_fake_action(fake_id, action):
    """Hide a player fake from the feed and its link, or show it again."""
    fake = db.session.get(PlayerFake, fake_id)
    if not is_admin(current_user()) or not fake or action not in ('hide', 'show'):
        abort(404)
    fake.status = 'hidden' if action == 'hide' else None
    if action == 'show':
        fake.reports = 0  # Looked at and fine: off the list until someone reports it again
    db.session.commit()
    return redirect(url_for('admin', _anchor='player-fakes'))


def delete_player_fakes(user):
    """Everything about player fakes that belongs to `user` (called when they delete their account)."""
    own = [f.id for f in PlayerFake.query.filter_by(user_id=user.id).with_entities(PlayerFake.id)]
    if own:
        PlayerFakePlay.query.filter(PlayerFakePlay.fake_id.in_(own)).delete(synchronize_session=False)
        PlayerFake.query.filter(PlayerFake.id.in_(own)).delete(synchronize_session=False)
    PlayerFakePlay.query.filter_by(user_id=user.id).delete(synchronize_session=False)
