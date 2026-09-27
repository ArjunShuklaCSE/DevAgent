from slugger import slugify


def test_empty_title_gives_empty_slug():
    assert slugify("") == ""


def test_symbol_only_title_gives_empty_slug():
    assert slugify("!!! ???") == ""
