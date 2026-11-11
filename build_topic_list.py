"""Build topics.json: more quiz topics per category, taken from Wikipedia's Vital articles (its curated list of
the ~10,000 articles an encyclopedia should have, sorted by subject), so the game isn't limited to the 20 hand-picked
topics per category in default_text.MENU.

    python build_topic_list.py

Only adds topics; MENU itself, and the knowledge base built from it, stay as they are. A topic from this list that
isn't in MENU has no saved copy, so if Wikipedia is down its quiz falls back to the AI's own knowledge.
Review the diff of topics.json before committing: the lists are maintained by Wikipedia editors and change.
"""
import json
import os
import re
import sys
import time

import requests

from default_text import MENU

API = "https://en.wikipedia.org/w/api.php"
VITAL = "Wikipedia:Vital articles/Level/4/"
OUT = os.path.join(os.path.dirname(__file__), "topics.json")
# Wikimedia asks automated clients for a User-Agent that says who they are
HEADERS = {"User-Agent": "WikiFakeFact/1.0 (https://www.wikifakefacts.com; educational quiz game) build_topic_list"}

# category: (Vital articles page, sections to take: a heading path starts with one of these)
SOURCES = {
    "Sports": ("Everyday life", [("Sports and recreation", "Sports", "Team sports"),
                                 ("Sports and recreation", "Sports", "Athletics"),
                                 ("Sports and recreation", "Sports", "Skating"),
                                 ("Sports and recreation", "Sports", "Water sports"),
                                 ("Sports and recreation", "Sports", "Combat sport and martial arts"),
                                 ("Sports and recreation", "Sports", "Other individual sports")]),
    "Animals": ("Biology and health sciences", [("Organisms", "Animals", s) for s in (
        "Cnidarians", "Echinoderms", "Mollusks", "Arthropods", "Invertebrates, others", "Vertebrates",
        "Animal breeds and hybrids")]),
    "Countries": ("Geography/Countries", [("Countries and other regions", "Countries", s) for s in (
        "Africa", "Asia", "Europe", "North America", "Oceania", "South America")]),
    "Planets and Space": ("Physical sciences", [("Astronomy", "Astronomical objects"),
                                                ("Astronomy", "Celestial mechanics and astrometry", "Constellations"),
                                                ("Astronomy", "Galactic astronomy and extragalactic astronomy"),
                                                ("Astronomy", "Physical cosmology"),
                                                ("Astronomy", "Planetary science"),
                                                ("Astronomy", "Stellar astronomy")]),
    "Technologies": ("Technology", [("Computing and information technology",), ("Electronics",),
                                    ("Media and communication",)]),
    "Musical Instruments": ("Arts", [("Music", "Musical instruments")]),
}
# Fruits have no section of their own: plants from levels 4 and 5 that Wikipedia files under a fruit category
FRUIT_PAGES = ["Level/4/Biology and health sciences", "Level/5/Biology and health sciences/Plants"]
NOT_FRUIT = {
    # fungi, grains, legumes, vegetables, nuts, palms and other plants with a fruit category
    "Chanterelle", "Mold", "Mushroom", "Puffball", "Truffle", "Maize", "Job's tears", "Legume", "Pea",
    "Chili pepper", "Jalapeño", "Cucumber", "Eggplant", "Pumpkin", "Summer squash", "Zucchini", "Calabash",
    "Almond", "Brazil nut", "Cashew", "Chestnut", "Sweet chestnut", "Macadamia", "Pecan", "Pistachio", "Walnut",
    "Carya ovata", "Wattleseed", "Cocoa bean", "Theobroma cacao", "Coffea", "Areca catechu", "Elaeis guineensis",
    "Borassus flabellifer", "Nypa fruticans", "Nelumbo nucifera", "Ficus sycomorus",
    # genera, families and scientific names of fruits already listed under their common name
    "Adansonia", "Adansonia digitata", "Annonaceae", "Diospyros", "Prunus", "Prunus avium", "Pyrus communis",
    "Pyrus pyrifolia", "Pyrus × bretschneideri", "Malus sieversii", "Sambucus", "Vaccinium myrtillus",
    "Vaccinium vitis-idaea", "Cucumis metuliferus", "Morus (plant)",
}
# Scientific group names (Coccinellidae, Scorpaeniformes, Hymenoptera …) make vague quizzes; species names stay
TAXON = re.compile(r"^\S+(idae|iformes|oidea|inae|ptera|poda|morpha|ata|ia|ae|ii|odea|ales|acea)$")
NOT_ANIMAL = {"Homo", "Australopithecus", "Cro-Magnon", "Paranthropus", "Homo erectus", "Homo habilis",
              "Neanderthal", "Denisovan", "Human", "Animal", "Tetrapod", "Invertebrate", "Vertebrate"}


def api(params):
    """One API call, waiting when Wikipedia says to slow down."""
    for _ in range(6):
        response = requests.get(API, params={**params, "format": "json", "formatversion": 2, "maxlag": 5},
                                headers=HEADERS, timeout=30)
        if response.ok and response.text.startswith("{"):
            return response.json()
        time.sleep(int(response.headers.get("Retry-After", 10)))
    sys.exit(f"Wikipedia kept refusing ({response.status_code}); try again later")


def listed_articles(page):
    """[(heading path, article title)] for every article on a Vital articles page."""
    wikitext = api({"action": "parse", "page": VITAL.replace("Level/4/", "") + page if page.startswith("Level/")
                    else VITAL + page, "redirects": 1, "prop": "wikitext"})["parse"]["wikitext"]
    path, articles = [], []
    for line in wikitext.splitlines():
        heading = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", line)
        if heading:
            name = re.sub(r"<[^>]+>", "", heading.group(2))
            name = re.sub(r"\s*\(\d[\d,]*\s*(articles?)?\)\s*$", "", name).strip()
            path = path[:len(heading.group(1)) - 2] + [name]
        elif re.match(r"^[#*]", line):
            link = re.search(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", line)
            if link and ":" not in link.group(1):
                articles.append((tuple(path), link.group(1).strip()))
    time.sleep(1)
    return articles


def fruits():
    titles = list(dict.fromkeys(title for page in FRUIT_PAGES for path, title in listed_articles(page)
                                if "Plants" in path or page.endswith("Plants")))
    found = []
    for i in range(0, len(titles), 50):
        pages = api({"action": "query", "titles": "|".join(titles[i:i + 50]), "prop": "categories",
                     "cllimit": "max", "clshow": "!hidden", "redirects": 1})["query"]["pages"]
        for page in pages:
            categories = " ".join(c["title"].lower() for c in page.get("categories", []))
            if ("fruit" in categories or "berries" in categories) and page["title"] not in NOT_FRUIT:
                found.append(page["title"])
        time.sleep(1)
    return sorted(found)


def keep(category, title):
    if category == "Animals":
        return title not in NOT_ANIMAL and not TAXON.match(title)
    return True


def main():
    known = {topic for topics in MENU.values() for topic in topics}
    topics = {}
    for category in MENU:
        if category == "Fruits":
            titles = fruits()
        else:
            page, sections = SOURCES[category]
            titles = [title for path, title in listed_articles(page)
                      if any(tuple(path[:len(s)]) == s for s in sections) and keep(category, title)]
        topics[category] = [t for t in dict.fromkeys(titles) if t not in known]
        print(f"{category}: {len(topics[category])} new topics")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=1)
        f.write("\n")


if __name__ == "__main__":
    main()
