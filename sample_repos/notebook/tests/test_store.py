from notebook.store import NoteStore


def test_search_exact_case():
    store = NoteStore()
    store.add("Groceries", "milk")
    assert [n.title for n in store.search("milk")] == ["Groceries"]
