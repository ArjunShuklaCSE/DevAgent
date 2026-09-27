from slugger import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_accents():
    assert slugify("Crème Brûlée") == "creme-brulee"


def test_leading_number():
    assert slugify("2024 plans") == "n2024-plans"
