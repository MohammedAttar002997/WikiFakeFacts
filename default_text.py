import json
import os

CATEGORIES = {
    "1": "Sports",
    "2": "Animals",
    "3": "Countries",
    "4": "Fruits",
    "5": "Planets and Space",
    "6": "Technologies",
    "7": "Musical Instruments",
    "8": "History",
    "9": "Science",
    "10": "Famous People",
    "11": "Cities",
}

MENU = {
    "Sports": [
        "Association football", "Basketball", "Tennis", "Cricket", "Baseball",
        "Volleyball", "Rugby football", "Golf", "Ice hockey", "Swimming (sport)",
        "Boxing", "Table tennis", "Badminton", "Cycling", "Archery",
        "Fencing", "Surfing", "Skateboarding", "Wrestling", "Skiing"
    ],
    "Animals": [
        "Cat", "Dog", "Elephant", "Tiger", "Lion",
        "Giraffe", "Zebra", "Kangaroo", "Giant panda", "Penguin",
        "Dolphin", "Wolf", "Red fox", "Rabbit", "Eagle",
        "Shark", "Octopus", "Horse", "Monkey", "Owl"
    ],
    "Countries": [
        "Germany", "Spain", "Italy", "France", "Brazil",
        "Japan", "Canada", "Australia", "Mexico", "India",
        "Egypt", "Argentina", "Greece", "Sweden", "Thailand",
        "Norway", "Portugal", "South Korea", "Turkey", "Netherlands"
    ],
    "Fruits": [
        "Apple", "Banana", "Orange (fruit)", "Strawberry", "Grape",
        "Mango", "Pineapple", "Blueberry", "Watermelon", "Kiwifruit",
        "Peach", "Pear", "Cherry", "Pomegranate", "Papaya",
        "Lemon", "Avocado", "Raspberry", "Plum", "Dragon fruit"
    ],
    "Planets and Space": [
        "Mercury (planet)", "Venus", "Earth", "Mars", "Jupiter",
        "Saturn", "Uranus", "Neptune", "Pluto", "Sun",
        "Moon", "Andromeda Galaxy", "Milky Way", "Black hole", "Nebula",
        "Asteroid", "Comet", "Supernova", "Quasar", "Exoplanet"
    ],
    "Technologies": [
        "Python (programming language)", "JavaScript", "Blockchain", "Artificial intelligence", "Robotics",
        "Cloud computing", "Internet of things", "Computer security", "Quantum computing", "Integrated circuit",
        "Smartphone", "Virtual reality", "Augmented reality", "Database", "Algorithm",
        "Compiler", "Encryption", "Biotechnology", "Nanotechnology", "Server (computing)"
    ],
    "Musical Instruments": [
        "Piano", "Guitar", "Violin", "Drum kit", "Saxophone",
        "Trumpet", "Flute", "Cello", "Clarinet", "Harp",
        "Accordion", "Banjo", "Harmonica", "Oboe", "Trombone",
        "Ukulele", "Synthesizer", "Xylophone", "Bagpipes", "Mandolin"
    ]
}


def load_extra_topics():
    """More topics per category from Wikipedia's Vital articles (topics.json, made by build_topic_list.py)."""
    with open(os.path.join(os.path.dirname(__file__), 'topics.json'), encoding='utf-8') as f:
        return json.load(f)


# Every topic a quiz can be about, per category: the hand-picked MENU first, then the Vital articles. MENU itself
# stays the list the knowledge base, the daily challenge and the weekly events use, since only its topics have a
# saved copy of their article for when Wikipedia is down.
TOPICS = {category: list(dict.fromkeys(MENU.get(category, []) + load_extra_topics().get(category, [])))
          for category in CATEGORIES.values()}
ALL_TOPICS = {topic for topics in TOPICS.values() for topic in topics}
SURPRISE = 'surprise'  # Setup's category value for a topic from any category

GAME_LENGTH_OPTIONS = {
    "1": "Short (3 rounds)",
    "2": "Medium (5 rounds)",
    "3": "Long (10 rounds)",
}

DIFFICULTY_OPTIONS = {
    "1": "Easy (Ages 7-12)",
    "2": "Medium (Ages 13-17)",
    "3": "Hard (Ages 18-99)",
}

LANGUAGE_OPTIONS = {
    "en": "English",
    "es": "Español (Spanish)",
    "fr": "Français (French)",
    "de": "Deutsch (German)",
    "it": "Italiano (Italian)",
    "pt": "Português (Portuguese)",
    "ar": "العربية (Arabic)",
}


LEADERBOARD_PERIODS = {'all': 'All time', 'week': 'Last 7 days', 'today': 'Today (UTC)'}
