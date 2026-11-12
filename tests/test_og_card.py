"""The preview image of a shared result (og_card.py), called directly."""
import io

from PIL import Image

from og_card import HEIGHT, WIDTH, drawable, result_card


def test_card_is_a_link_preview_sized_png():
    with Image.open(io.BytesIO(result_card('Asmaa', 4, 5, 'Milky Way'))) as image:
        assert image.format == 'PNG' and image.size == (WIDTH, HEIGHT) == (1200, 630)


def test_only_latin_text_is_drawn():
    assert drawable('Asmaa') and drawable('Zoë Müller') and drawable('Swimming (sport)')
    assert not drawable('محمد') and not drawable('日本') and not drawable('')


def test_any_name_topic_and_score_make_a_card():
    for name, score, total, topic in [('محمد', 2, 3, 'Cat'), ('A' * 200, 0, 10, 'B' * 300), ('Lena', 3, 3, 'Ōsaka'),
                                      ('', 0, 0, '')]:
        assert result_card(name, score, total, topic)[:4] == b'\x89PNG'
