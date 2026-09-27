from kvconfig import parse


def test_value_may_contain_equals():
    assert parse("url = https://x.test/?a=1&b=2") == {"url": "https://x.test/?a=1&b=2"}


def test_base64_padding_is_kept():
    assert parse("token=YWJj==") == {"token": "YWJj=="}
