from tagstore.items import Catalog, Item


def test_items_do_not_share_default_tags():
    first = Item("a")
    first.add_tag("x")
    assert Item("b").tags == []


def test_catalog_items_without_tags_are_independent():
    catalog = Catalog()
    catalog.add("a").add_tag("red")
    catalog.add("b")
    assert catalog.with_tag("red") == ["a"]
