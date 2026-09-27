from notebook.store import NoteStore


def test_search_ignores_case_in_title():
    store = NoteStore()
    store.add("Groceries", "milk, eggs")
    assert [n.title for n in store.search("groceries")] == ["Groceries"]


def test_search_ignores_case_in_body():
    store = NoteStore()
    store.add("List", "Buy MILK")
    assert [n.title for n in store.search("milk")] == ["List"]
