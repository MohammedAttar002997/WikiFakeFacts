"""Ready-made quizzes: the rules for which ones to make ahead of time (no database, no AI).

A timer calls /cron/refill (Vercel once a day, GitHub Actions every 30 minutes). Each call makes a few quizzes of
the kinds players asked for lately, so the next player who asks for that kind starts at once instead of waiting
for the AI. A ready quiz is handed to one player only, then the stock is topped up again on a later call.

A "kind" is what the setup page asks for: category, difficulty, number of rounds and language. The topic inside the
category is random anyway, so a ready quiz of the right kind is as good as a fresh one.
"""
from collections import namedtuple

Kind = namedtuple('Kind', 'category difficulty rounds language')

TARGET_STOCK = 2   # Ready quizzes kept per kind
DAILY_CAP = 20     # Quizzes made ahead in any 24 hours, however many kinds are wanted (each costs an AI call)
WANTED_DAYS = 7    # A kind nobody asked for in this many days isn't made any more
RUN_SECONDS = 25   # One call stops starting new quizzes after this long, so it ends well inside the time limit


def refill_plan(wanted, stock, made_last_day, cap=DAILY_CAP, target=TARGET_STOCK):
    """The kinds to make now, in order.

    wanted: [(Kind, when it was last asked for)], only kinds asked for lately.
    stock: {Kind: ready quizzes nobody has taken yet}.
    made_last_day: quizzes already made ahead in the last 24 hours.

    Kinds are filled level by level (every empty kind gets one before any gets a second), most recently wanted
    first, so a single popular kind can't use up the day's budget while others stay empty.
    """
    budget = max(0, cap - made_last_day)
    kinds = [kind for kind, _ in sorted(wanted, key=lambda w: w[1], reverse=True)]
    have = {kind: stock.get(kind, 0) for kind in kinds}
    plan = []
    for level in range(1, target + 1):
        for kind in kinds:
            if len(plan) >= budget:
                return plan
            if have[kind] < level:
                plan.append(kind)
                have[kind] += 1
    return plan
