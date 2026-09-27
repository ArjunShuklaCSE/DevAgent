from tagstore.items import Catalog


def test_add_with_tags():
    catalog = Catalog()
    catalog.add("apple", ["fruit"])
    assert catalog.with_tag("fruit") == ["apple"]
