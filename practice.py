"""Practice your mistakes: a quiz made only of rounds the player got wrong before (pure rules, no database, no AI).

A round counts as a mistake when the player picked a true statement or ran out of time. Once they get it right in a
practice quiz it's done and isn't offered again; rounds the owner hid after a report are never offered. Practice
quizzes earn no score, badges or leaderboard points: the player has already seen the answer.
"""
import random

from hidden_rounds import fingerprint

PRACTICE_ROUNDS = 5   # Rounds per practice quiz (fewer if there aren't that many mistakes)
GAMES_LOOKED_AT = 100  # Newest finished games searched for mistakes (history and results pages count them)


def mistakes(games, practice_games, hidden):
    """[(round, explanation, quote)] still to practice, newest mistake first, each round once.

    games: finished normal games, newest first, as (rounds, answers, explanations, quotes).
    practice_games: finished practice quizzes in the same form; a round answered right there is done.
    hidden: fingerprints of rounds hidden after a report.
    """
    done = set(hidden)
    for rounds, answers, _, _ in practice_games:
        done.update(fingerprint(r) for r, pick in zip(rounds, answers) if r.get(pick) is False)
    found, seen = [], set()
    for rounds, answers, explanations, quotes in games:
        for i, (statements, pick) in enumerate(zip(rounds, answers)):
            key = fingerprint(statements)
            if statements.get(pick) is False or key in done or key in seen:
                continue
            seen.add(key)
            found.append((statements, at(explanations, i), at(quotes, i)))
    return found


def choose(found, n=PRACTICE_ROUNDS, rng=random):
    """The rounds of the next practice quiz: the newest `n` mistakes, in a shuffled order, as
    (rounds, explanations, quotes)."""
    picked = list(found[:n])
    rng.shuffle(picked)
    return [r for r, _, _ in picked], [e for _, e, _ in picked], [q for _, _, q in picked]


def at(values, i):
    return values[i] if values and i < len(values) else None
