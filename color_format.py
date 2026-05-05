COLOR_FORMAT_MAP = {
    # --- ORIGINAL & UI STATES ---
    "user_choice_format": ['\x1b[6;30;42m', '\x1b[0m'],
    "wrong_choice_format": ['\x1b[0;30;47m', '\x1b[0m'],
    "success": ['\x1b[1;32m', '\x1b[0m'],
    "error": ['\x1b[1;31m', '\x1b[0m'],
    "warning": ['\x1b[1;33m', '\x1b[0m'],
    "info": ['\x1b[1;34m', '\x1b[0m'],
    "header": ['\x1b[1;95m', '\x1b[0m'],

    # --- BRIGHT TEXT COLORS (No Background) ---
    "bright_white": ['\x1b[1;97m', '\x1b[0m'],
    "bright_yellow": ['\x1b[1;93m', '\x1b[0m'],
    "bright_cyan": ['\x1b[1;96m', '\x1b[0m'],
    "bright_magenta": ['\x1b[1;95m', '\x1b[0m'],
    "bright_green": ['\x1b[1;92m', '\x1b[0m'],
    "orange_text": ['\x1b[38;5;208m', '\x1b[0m'],  # Extended 256-color code

    # --- VIBRANT BACKGROUNDS (Black Text) ---
    "bg_cyan": ['\x1b[0;30;46m', '\x1b[0m'],
    "bg_yellow": ['\x1b[0;30;43m', '\x1b[0m'],
    "bg_red": ['\x1b[0;30;41m', '\x1b[0m'],
    "bg_blue": ['\x1b[0;37;44m', '\x1b[0m'],
    "bg_magenta": ['\x1b[0;30;45m', '\x1b[0m'],
    "bg_green": ['\x1b[0;30;42m', '\x1b[0m'],
    "bg_bright_white": ['\x1b[0;30;107m', '\x1b[0m'],

    # --- SPECIAL GAME UI COMBOS ---
    "gold_title": ['\x1b[1;33;100m', '\x1b[0m'],  # Bold Gold on Dark Gray
    "danger_alert": ['\x1b[1;97;41m', '\x1b[0m'],  # Bold White on Red
    "ocean_theme": ['\x1b[1;97;104m', '\x1b[0m'],  # Bold White on Light Blue
    "forest_theme": ['\x1b[1;97;42m', '\x1b[0m'],  # Bold White on Green
    "neon_pink": ['\x1b[1;95;107m', '\x1b[0m'],  # Magenta on White

    # --- STYLES ---
    "dim": ['\x1b[2m', '\x1b[0m'],
    "underline": ['\x1b[4m', '\x1b[0m'],
    "inverse": ['\x1b[7m', '\x1b[0m'],
    "italic": ['\x1b[3m', '\x1b[0m'],  # Note: Support varies by terminal
}