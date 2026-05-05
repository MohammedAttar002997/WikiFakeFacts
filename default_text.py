# WELCOME_TEXT = "Welcome to WikiFakeFact game\n"
from color_format import COLOR_FORMAT_MAP

GAME_RULES_TEXT = """In this game, 
you will be given a question on a specific topic and four different facts. 
One of these facts is fake. 
Your task is to identify the fake one in each round.
Let’s go!
"""

CONST_PROMPT_TEXT = """f"Each object must contain exactly 4 entries:\n
            - 3 real facts from the article (max 100 chars each).\n
            - 1 fake fact that sounds highly plausible (max 100 chars each).\n\n
            Format: Return a list of dictionaries where the key is the string statement 
            and the value is a boolean (true for real, false for fake).\n
            Constraint: Provide ONLY the JSON array. No conversational text."""

ERROR_MESSAGE_TEXT = "Invalid input. Please choose an option between 1 and "

WELCOME_TEXT = r"""
 ██     ██  ██  ██   ██  ██    ███████  █████  ██   ██ ███████    ███████  █████   ██████ ████████
 ██     ██  ██  ██  ▄██  ██    ██      ██   ██ ██  ▄██ ██         ██      ██   ██ ██         ██
 ██  █  ██  ██  █████    ██    █████   ███████ █████   █████      █████   ███████ ██         ██
 ██ ███ ██  ██  ██  ██   ██    ██      ██   ██ ██  ██  ██         ██      ██   ██ ██         ██
  ███ ███   ██  ██   ██  ██    ██      ██   ██ ██   ██ ███████    ██      ██   ██  ██████    ██
"""


CATEGORIES = {
    "1": "Sports",
    "2": "Animals",
    "3": "Countries",
    "4": "Fruits",
    "5": "Planets and Space",
    "6": "Technologies",
    "7": "Musical Instruments",
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
}


def show_error_message(num_len, options=""):
   return COLOR_FORMAT_MAP["error"][0]+ERROR_MESSAGE_TEXT + f"{num_len}{options} " + COLOR_FORMAT_MAP["error"][1]


def show_available_options(option_number, option_value):
    return option_number + ": " + option_value