"""In-memory catalog of tagged items."""


class Item:
    def __init__(self, name, tags=[]):
        self.name = name
        self.tags = tags

    def add_tag(self, tag):
        if tag not in self.tags:
            self.tags.append(tag)


class Catalog:
    def __init__(self):
        self._items = {}

    def add(self, name, tags=None):
        item = Item(name, tags) if tags is not None else Item(name)
        self._items[name] = item
        return item

    def with_tag(self, tag):
        return sorted(name for name, item in self._items.items() if tag in item.tags)
