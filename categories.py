from default_text import show_error_message, CATEGORIES, MENU, show_available_options, GAME_LENGTH_OPTIONS


def show_options(title, options_dict):
    error_message = show_error_message(len(options_dict))
    for key, val in options_dict.items():
        print(show_available_options(key, val))
    print(" ")
    try:
        selected_option = input(title)
        if selected_option in options_dict.keys():
            return options_dict[selected_option]
        else:
            print()
            print(f"{error_message}.\n")
            return show_options(title, options_dict)
    except ValueError:
        print(f"{error_message}.\n")
        show_options(title, options_dict)


def show_categories():
    print("Available categories:")
    print("---------------------")
    selected_category = show_options("Please choose a category: ", CATEGORIES)
    return MENU[selected_category]


def show_game_length():
    print("Available game lengths:")
    print("---------------------")
    game_length = show_options("Would you like a short, medium, or a long game? ", GAME_LENGTH_OPTIONS)
    return int(game_length.split("rounds")[0].split("(")[1].strip())


