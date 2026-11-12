"""The link-preview image of a shared result (1200x630 PNG): who spotted how many fake facts on which topic.

Drawn with Pillow's own font, so no font file has to ship with the app. That font has no Arabic (and Pillow here
has no text shaping), so names and topics in other scripts are left out rather than drawn as empty boxes.
"""
import io

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
PURPLE, PINK, NIGHT = (124, 58, 237), (219, 39, 119), (15, 12, 29)
WHITE, SOFT, AMBER = (255, 255, 255), (233, 213, 255), (245, 158, 11)
RING_TRACK = (88, 48, 140)


def drawable(text):
    """Whether the bundled font can draw `text`: Latin script, digits and common punctuation."""
    return bool(text) and all(ord(ch) < 0x250 or ch in '–—‘’“”…·' for ch in text)


def fit(draw, text, font, width):
    """`text`, cut with an ellipsis so it fits in `width` pixels."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + '…', font=font) > width:
        text = text[:-1]
    return text.rstrip() + '…'


def font(size):
    return ImageFont.load_default(size=size)


def background():
    """Night blue with the purple-to-pink glow of the site's hero."""
    image = Image.new('RGB', (WIDTH, HEIGHT), NIGHT)
    glow = Image.linear_gradient('L').rotate(90).resize((WIDTH, HEIGHT))  # 255 on the left, 0 on the right
    purple = Image.new('RGB', (WIDTH, HEIGHT), PURPLE)
    pink = Image.new('RGB', (WIDTH, HEIGHT), PINK)
    image = Image.composite(purple, pink, glow)
    # Darken toward the bottom so the text stands out
    shade = Image.linear_gradient('L').resize((WIDTH, HEIGHT)).point(lambda v: int(v * 0.55))
    return Image.composite(Image.new('RGB', (WIDTH, HEIGHT), NIGHT), image, shade)


def result_card(name, score, total, topic):
    """PNG bytes of the card."""
    image = background()
    draw = ImageDraw.Draw(image)

    # Score ring on the left
    cx, cy, r, ring = 280, 315, 170, 26
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.ellipse(box, outline=RING_TRACK, width=ring)  # the part still to fill
    share = score / total if total else 0
    if share:
        draw.arc(box, start=-90, end=-90 + 360 * share, fill=AMBER, width=ring)
    big = font(120)
    label = f'{score}/{total}'
    draw.text((cx, cy - 10), label, font=big, fill=WHITE, anchor='mm', stroke_width=3, stroke_fill=WHITE)
    draw.text((cx, cy + 75), 'fake facts', font=font(34), fill=SOFT, anchor='mm')

    # Text on the right
    x, width = 520, WIDTH - 520 - 70
    who = name if drawable(name) else 'A player'
    draw.text((x, 150), fit(draw, who, font(76), width), font=font(76), fill=WHITE, stroke_width=2, stroke_fill=WHITE)
    draw.text((x, 250), f'spotted {score} of {total} fake facts', font=font(44), fill=WHITE)
    if drawable(topic):
        draw.text((x, 318), fit(draw, f'Topic: {topic}', font(38), width), font=font(38), fill=SOFT)
    pill = font(36)
    text = 'Can you beat that?'
    pw = draw.textlength(text, font=pill) + 56
    draw.rounded_rectangle((x, 400, x + pw, 470), radius=35, fill=WHITE)
    draw.text((x + pw / 2, 435), text, font=pill, fill=(46, 16, 101), anchor='mm', stroke_width=1, stroke_fill=(46, 16, 101))

    # Footer
    draw.text((x, 540), 'WikiFakeFact', font=font(40), fill=WHITE, stroke_width=1, stroke_fill=WHITE)
    draw.text((WIDTH - 70, 545), 'wikifakefacts.com', font=font(32), fill=SOFT, anchor='ra')

    out = io.BytesIO()
    image.save(out, 'PNG', optimize=True)
    return out.getvalue()
