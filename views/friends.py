"""Friends (rules in friends.py): follow and unfollow from a profile, and the Friends tab of the Players page with a
leaderboard of this week and a feed of followed players' games. Routes register on the app when app.py imports this.
"""
from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import app as core
import friends as rules
import leagues
from app import app, current_user, db, event_week, local_path, over_limit
from models import Follow, QuizSession, Score, User
from translations import translate as _

PROBLEMS = {
    'self': lambda: _("You can't follow yourself."),
    'already': lambda: _('You already follow this player.'),
    'full': lambda: _('You follow as many players as you can. Unfollow someone first.'),
}


def following_ids(user):
    return {fid for (fid,) in db.session.query(Follow.followed_id).filter(Follow.follower_id == user.id)}


def follower_count(user):
    return Follow.query.filter_by(followed_id=user.id).count()


def is_following(user, other):
    return bool(user) and Follow.query.filter_by(follower_id=user.id, followed_id=other.id).first() is not None


app.jinja_env.globals.update(is_following=is_following, follower_count=follower_count)


@app.route('/players/<int:user_id>/follow', methods=['POST'])
def follow(user_id):
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    other = db.get_or_404(User, user_id)
    back = local_path(request.form.get('next')) or url_for('player_profile', user_id=other.id)
    if over_limit('follow', user.id):
        flash(_("You're going a bit fast. Please wait a few minutes and try again."), 'warning')
        return redirect(back)
    problem = rules.can_follow(user.id, other.id, is_following(user, other),
                               Follow.query.filter_by(follower_id=user.id).count())
    if problem:
        flash(PROBLEMS[problem](), 'info')
        return redirect(back)
    db.session.add(Follow(follower_id=user.id, followed_id=other.id, created_at=core.utcnow()))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()  # A double click: the first one counts
    flash(_('You now follow {name}.').format(name=other.username), 'success')
    return redirect(back)


@app.route('/players/<int:user_id>/unfollow', methods=['POST'])
def unfollow(user_id):
    user = current_user()
    if not user:
        return redirect(url_for('index'))
    other = db.get_or_404(User, user_id)
    Follow.query.filter_by(follower_id=user.id, followed_id=other.id).delete()
    db.session.commit()
    return redirect(local_path(request.form.get('next')) or url_for('player_profile', user_id=other.id))


def week_points(user_ids, week):
    """{user id: (league points, games)} this week for the given players."""
    start, end = leagues.week_range(week)
    totals = {uid: [0, 0] for uid in user_ids}
    for uid, difficulty, correct in (db.session.query(Score.user_id, Score.difficulty, Score.score)
                                     .filter(Score.user_id.in_(user_ids), Score.timestamp >= start, Score.timestamp < end)):
        totals[uid][0] += leagues.points_for(difficulty, correct)
        totals[uid][1] += 1
    return {uid: tuple(t) for uid, t in totals.items()}


@app.route('/friends')
def friends_page():
    user = current_user()
    if not user:
        return render_template('friends.html', board=[], feed=[], suggested=[], users={}, following=set())
    following = following_ids(user)
    week = event_week(core.utcnow().date())
    ids = following | {user.id}
    points = week_points(ids, week)
    board = rules.leaderboard([(uid, *points[uid]) for uid in ids], user.id)
    feed = []
    if following:
        feed = (db.session.query(Score, QuizSession.share_code)
                .outerjoin(QuizSession, Score.quiz_session_id == QuizSession.id)
                .filter(Score.user_id.in_(following)).order_by(Score.timestamp.desc()).limit(rules.FEED_SIZE).all())
    start, end = leagues.week_range(week)
    active = [uid for uid, _ in (db.session.query(Score.user_id, func.count(Score.id))
                                 .filter(Score.timestamp >= start, Score.timestamp < end)
                                 .group_by(Score.user_id).order_by(func.count(Score.id).desc()).limit(50))]
    suggested = rules.suggestions(active, following, user.id) if len(following) < 3 else []
    wanted = ids | {s.user_id for s, _ in feed} | set(suggested)
    users = {u.id: u for u in User.query.filter(User.id.in_(wanted))}
    return render_template('friends.html', board=board, feed=feed, suggested=suggested, users=users,
                           following=following, followers=follower_count(user))


def forget_player(user):
    """A deleted player's follows go both ways."""
    Follow.query.filter((Follow.follower_id == user.id) | (Follow.followed_id == user.id)).delete(synchronize_session=False)
