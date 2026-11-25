"""What Finn, the help chat (chatbot.py), knows about WikiFakeFact: every feature, how to use it and where it is.

Plain data, no Flask, so tests can check it: every page named here must exist in the app (tests/test_help_chat.py),
and the numbers come from the settings that decide them, so the guide can't drift from the app. When a feature is
added, changed or removed, change its entry here too.
"""
from collections import namedtuple

import leagues
from default_text import DIFFICULTY_OPTIONS, GAME_LENGTH_OPTIONS, TOPICS

Feature = namedtuple('Feature', 'name how url')


def features(timed_seconds, event_best_of, max_file_mb):
    """The guide's entries. The arguments are settings that live in app.py, passed in to keep this module pure."""
    lengths = ', '.join(GAME_LENGTH_OPTIONS.values())
    levels = ', '.join(DIFFICULTY_OPTIONS.values())
    points = ', '.join(f'{level.split(" (")[0]} {n}' for level, n in leagues.POINTS.items())
    return [
        Feature('How a round works', 'Each round shows four statements from a Wikipedia article: three are true and '
                'one was changed to be fake. Pick the fake. After the quiz, the answer review shows the fake, why it '
                'is fake, the sentence from Wikipedia it came from and what the fake changed.', '/setup'),
        Feature('Signing in', 'On the home page: pick a name and a 4-digit PIN. No email needed. Coming back, use the '
                'same name and PIN.', '/'),
        Feature('Try a round without an account', 'One free round, no sign-up.', '/try'),
        Feature('Play a quiz', f'Game setup: pick a category ({", ".join(TOPICS)}), "Surprise me" for a random '
                f'category, or "Any topic" to search all of Wikipedia. Then the length ({lengths}), the difficulty '
                f'({levels}) and the quiz language. "Timed mode" gives {timed_seconds} seconds per round; running out '
                'counts as a miss.', '/setup'),
        Feature('Browse topics', 'Every topic by category, each with a page and a sample round to try.', '/topics'),
        Feature('Daily challenge', 'One quiz, the same for everyone, once a day, one attempt. A new one starts at '
                'midnight UTC. There is a daily ranking, and daily reminders (notifications) can be switched on '
                'there.', '/daily'),
        Feature('Weekly event', f'A new theme every Monday. Your {event_best_of} best event quizzes of the week '
                'count for the event leaderboard.', '/event'),
        Feature('Weekly league', f'Finishing any quiz puts you in this week\'s league: groups of up to '
                f'{leagues.GROUP_SIZE} players in {", ".join(leagues.TIERS)}. Points per correct answer: {points}. '
                'At the end of the week the top of each group moves up a league and the bottom moves down.',
                '/league'),
        Feature('Challenge friends', 'After a quiz, share your result: friends who open the link play the same '
                'quiz. The Challenges page shows who took them, lets you compare answers round by round, and has '
                'rematches.', '/challenges'),
        Feature('Live match', 'On game setup, "Play live with a friend": you both answer the same rounds at the same '
                'time; right answers score and faster ones score more. Share the link with your friend.', '/setup'),
        Feature('Class game', 'On game setup, "Play with a class": the teacher shows the game on a big screen and '
                'students join on their phones at the join page with a code and a nickname, no account needed. The '
                'teacher can download the results.', '/join'),
        Feature('Player fakes', 'Write your own fake: in an answer review, "Write your own fake for this round". '
                'Other players then try to spot it. The Player fakes page lists them to play.', '/fakes'),
        Feature('Practice your mistakes', 'After a quiz or on My Progress: a practice round made of rounds you got '
                'wrong before. No score, no AI wait.', '/analysis'),
        Feature('Custom quiz', f'Make a quiz from your own content: upload a PDF, TXT or DOCX file (up to '
                f'{max_file_mb} MB), paste text, or paste a link to a web page. Earlier uploads can be replayed. '
                'Scanned PDFs (photos of pages) have no text to read, so paste the text instead.', '/custom_quiz'),
        Feature('My progress', 'An AI analysis of how you play: strongest and weakest topics, and tips. Past quizzes '
                'are in the history.', '/analysis'),
        Feature('Quiz history', 'Every quiz you played, with the answer review.', '/history'),
        Feature('Leaderboard', 'The best players overall.', '/leaderboard'),
        Feature('Players', 'All players and their profiles. Follow players from their profile.', '/players'),
        Feature('Friends', 'This week\'s points of you and the players you follow, and their latest games.',
                '/friends'),
        Feature('Your account', 'Change your name, PIN, photo and favorite topic, dress Finn in outfits unlocked by '
                'badges, or delete your account and data.', '/account'),
        Feature('Badges', 'Earned by playing: first quiz, 10 and 50 quizzes, a perfect quiz, 80% on Hard, 3 and 7 '
                'day streaks, every category, a daily challenge, an event, your own content, a comeback, writing '
                'fakes. They show on your profile and unlock outfits for Finn.', '/account'),
        Feature('Report a wrong round', 'In the answer review of your own quiz, "Report a problem" under the round.',
                '/history'),
        Feature('Put a round on your website', 'A widget with a round of the day for blogs and school sites; copy '
                'the code.', '/embed'),
        Feature('Language', 'The site comes in English, Spanish, French, German, Italian, Portuguese and Arabic: '
                'use the language menu at the top. Quizzes can be in any of them too.', '/setup'),
    ]


def guide_text(entries):
    """The guide as Finn reads it."""
    return '\n'.join(f'- {f.name} ({f.url}): {f.how}' for f in entries)


def known_urls(entries):
    return {f.url for f in entries}
