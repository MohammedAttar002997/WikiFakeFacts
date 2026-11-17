"""Finn's wardrobe: what he can wear and which badge unlocks it (pure rules, no database, no Flask).

A player dresses "their" Finn on the account page: a hat, a scarf, a coat and one extra. Every item except the
defaults is unlocked by a badge (badges.py), so badges are worth something you can see. The choice is stored on the
player as text like 'hat=navy;scarf=blue' (User.finn_outfit) and Finn wears it wherever that player sees him.

Colours reach the drawing as CSS variables on the <svg> (templates/_finn.html reads them in app.css); the extra is a
class that shows one of the accessories drawn into it.
"""

# slot -> [(key, name, badge that unlocks it or None, {CSS variable: colour})], the first of each slot is the default
SLOTS = {
    'hat': [
        ('classic', 'Classic brown', None, {}),
        ('navy', 'Navy blue', 'quiz_10', {'--finn-hat': '#27407a', '--finn-hat-brim': '#1c2f5c', '--finn-hat-band': '#e1b866'}),
        ('green', 'Forest green', 'explorer', {'--finn-hat': '#2f7d4f', '--finn-hat-brim': '#215c39', '--finn-hat-band': '#f4e3b0'}),
        ('gold', 'Gold', 'quiz_50', {'--finn-hat': '#d9a521', '--finn-hat-brim': '#b08314', '--finn-hat-band': '#7a2a1a'}),
    ],
    'scarf': [
        ('red', 'Red', None, {}),
        ('blue', 'Blue', 'daily', {'--finn-scarf': '#2f6fd6', '--finn-scarf-dark': '#2458ad'}),
        ('purple', 'Purple', 'streak_3', {'--finn-scarf': '#8b5cf6', '--finn-scarf-dark': '#6d43d1'}),
        ('yellow', 'Sunny yellow', 'streak_7', {'--finn-scarf': '#f2c230', '--finn-scarf-dark': '#cf9f15'}),
    ],
    'coat': [
        ('brown', 'Brown', None, {}),
        ('grey', 'Grey', 'hard_ace', {'--finn-coat': '#6b7280', '--finn-coat-dark': '#565c69', '--finn-coat-light': '#8b93a1'}),
        ('midnight', 'Midnight', 'perfect', {'--finn-coat': '#2b2f4a', '--finn-coat-dark': '#202339', '--finn-coat-light': '#444a70'}),
    ],
    'extra': [
        ('none', 'Nothing', None, {}),
        ('glasses', 'Glasses', 'comeback', {}),
        ('bowtie', 'Bow tie', 'event', {}),
        ('pencil', 'Pencil behind the ear', 'fake_writer', {}),
        ('star', 'Star pin', 'master_faker', {}),
    ],
}
SLOT_NAMES = {'hat': 'Hat', 'scarf': 'Scarf', 'coat': 'Coat', 'extra': 'Extra'}
DEFAULTS = {slot: items[0][0] for slot, items in SLOTS.items()}
ITEMS = {slot: {key: (name, badge, colours) for key, name, badge, colours in items} for slot, items in SLOTS.items()}


def unlocked(slot, key, badges):
    """Whether a player with these badge keys may wear the item."""
    item = ITEMS.get(slot, {}).get(key)
    return bool(item) and (item[1] is None or item[1] in badges)


def parse(text):
    """{slot: key} from the stored text; unknown slots and items fall back to the defaults."""
    chosen = dict(DEFAULTS)
    for part in (text or '').split(';'):
        slot, _, key = part.partition('=')
        if key in ITEMS.get(slot, {}):
            chosen[slot] = key
    return chosen


def allowed(choice, badges):
    """The outfit a player may actually wear: each chosen item if it's unlocked, else that slot's default. Used when
    saving (a form can ask for anything) and when showing (a badge can't be lost, but the rules may change)."""
    return {slot: (choice.get(slot) if unlocked(slot, choice.get(slot), badges) else default)
            for slot, default in DEFAULTS.items()}


def serialize(outfit):
    """The stored text: only what differs from the defaults ('' for a plain Finn)."""
    return ';'.join(f'{slot}={outfit[slot]}' for slot in SLOTS if outfit.get(slot, DEFAULTS[slot]) != DEFAULTS[slot])


def style(outfit):
    """The CSS variables for the outfit's colours, as the <svg>'s style attribute ('' for the defaults)."""
    variables = {}
    for slot in SLOTS:
        variables.update(ITEMS[slot][outfit.get(slot, DEFAULTS[slot])][2])
    return ';'.join(f'{name}:{colour}' for name, colour in variables.items())


def extra(outfit):
    """The accessory to show, or '' for none."""
    key = outfit.get('extra', 'none')
    return '' if key == 'none' else key
