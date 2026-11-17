"""Weekly leagues (rules in leagues.py): joining a week's league with the first finished game, the standings of a
group, and the /league page. Routes register on the app when app.py imports this."""
from flask import render_template
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import app as core
import leagues as rules
from app import app, current_user, db, event_week
from models import LeagueEntry, Score, User


def entry_for(user, week):
    return LeagueEntry.query.filter_by(user_id=user.id, week=week).first()


def group_standings(week, tier, group_no):
    """[{rank, user, points, result}] of a group, best first; points are worked out from that week's scores."""
    entries = (LeagueEntry.query.filter_by(week=week, tier=tier, group_no=group_no)
               .order_by(LeagueEntry.id).all())
    if not entries:
        return []
    ids = [e.user_id for e in entries]
    start, end = rules.week_range(week)
    points = dict.fromkeys(ids, 0)
    for user_id, difficulty, correct in (db.session.query(Score.user_id, Score.difficulty, Score.score)
                                         .filter(Score.user_id.in_(ids), Score.timestamp >= start,
                                                 Score.timestamp < end)):
        points[user_id] += rules.points_for(difficulty, correct)
    users = {u.id: u for u in User.query.filter(User.id.in_(ids))}
    ranked = rules.standings([(user_id, points[user_id]) for user_id in ids])
    return [{'rank': rank, 'user': users.get(user_id), 'points': pts,
             'result': rules.outcome(rank, pts, len(ranked), tier)}
            for rank, user_id, pts in ranked if user_id in users]


def last_result(user, before_week):
    """Where the player finished in the last week they played before `before_week`: {week, tier, rank, size, result}
    or None for someone new to the leagues."""
    last = (LeagueEntry.query.filter(LeagueEntry.user_id == user.id, LeagueEntry.week < before_week)
            .order_by(LeagueEntry.week.desc()).first())
    if not last:
        return None
    table = group_standings(last.week, last.tier, last.group_no)
    mine = next((row for row in table if row['user'] and row['user'].id == user.id), None)
    if not mine:
        return None
    return {'week': last.week, 'tier': last.tier, 'rank': mine['rank'], 'size': len(table), 'result': mine['result']}


def tier_for(user, week):
    """The tier the player is in for `week`: up or down from where they finished last time, Bronze for newcomers."""
    last = last_result(user, week)
    return rules.next_tier(last['tier'], last['result']) if last else 0


def join_league(user):
    """Put the player in this week's league, if they aren't yet (called when they finish a game). Returns the entry."""
    week = event_week(core.utcnow().date())
    entry = entry_for(user, week)
    if entry:
        return entry
    tier = tier_for(user, week)
    sizes = [n for _, n in (db.session.query(LeagueEntry.group_no, func.count(LeagueEntry.id))
                            .filter_by(week=week, tier=tier).group_by(LeagueEntry.group_no)
                            .order_by(LeagueEntry.group_no))]
    entry = LeagueEntry(user_id=user.id, week=week, tier=tier, group_no=rules.next_group(sizes))
    db.session.add(entry)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()  # Two games finished at the same moment: the first entry stands
        entry = entry_for(user, week)
    return entry


@app.route('/league')
def league():
    user = current_user()
    today = core.utcnow().date()
    week = event_week(today)
    entry = entry_for(user, week) if user else None
    table = group_standings(week, entry.tier, entry.group_no) if entry else []
    tier = entry.tier if entry else (tier_for(user, week) if user else 0)
    up, down = rules.zones(len(table), tier)
    return render_template('league.html', entry=entry, table=table, tier=tier, tiers=rules.TIERS,
                           icons=rules.TIER_ICONS, up=up, down=down, days_left=6 - today.weekday(),
                           last=last_result(user, week) if user else None, points=rules.POINTS,
                           group_size=rules.GROUP_SIZE,
                           my_row=next((r for r in table if user and r['user'].id == user.id), None))


def forget_player(user):
    """A deleted player leaves the leagues too."""
    LeagueEntry.query.filter_by(user_id=user.id).delete(synchronize_session=False)
