import pytest

from sandbox.junit import JUnitError, parse_junit
from sandbox.output import BoundedBuffer

PYTEST_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="1" failures="1" skipped="1" tests="4" time="0.05">
<testcase classname="tests.test_chunk" name="test_ok" time="0.001"/>
<testcase classname="tests.test_chunk" name="test_last" time="0.002"><failure message="AssertionError: assert [[1, 2]] == [[1, 2], [3]]">def test_last():
&gt;   assert chunk([1, 2, 3], 2) == [[1, 2], [3]]</failure></testcase>
<testcase classname="tests.test_chunk" name="test_fixture" time="0"><error message="fixture 'db' not found"/></testcase>
<testcase classname="tests.test_chunk" name="test_skip" time="0"><skipped type="pytest.skip" message="later"/></testcase>
</testsuite></testsuites>"""  # noqa: E501


def test_parses_pytest_report() -> None:
    report = parse_junit(PYTEST_XML)
    assert (report.total, report.passed, report.failed, report.errors, report.skipped) == (
        4,
        1,
        1,
        1,
        1,
    )
    assert not report.succeeded
    failing = report.failing()
    assert [c.test_id for c in failing] == [
        "tests.test_chunk::test_last",
        "tests.test_chunk::test_fixture",
    ]
    assert failing[0].message is not None
    assert "assert chunk([1, 2, 3], 2)" in failing[0].message


def test_all_passing_single_suite_root() -> None:
    report = parse_junit(b'<testsuite><testcase classname="t" name="a" time="0.5"/></testsuite>')
    assert report.succeeded
    assert report.duration_seconds == 0.5


def test_empty_report_is_not_success() -> None:
    assert not parse_junit(b"<testsuites/>").succeeded


@pytest.mark.parametrize(
    "data",
    [
        b"not xml",
        b"<html/>",
        # billion laughs: rejected by defusedxml, never expanded
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        b"<testsuites>&b;</testsuites>",
        # external entity
        b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<testsuites>&x;</testsuites>",
    ],
)
def test_rejects_invalid_or_hostile_xml(data: bytes) -> None:
    with pytest.raises(JUnitError):
        parse_junit(data)


def test_bounded_buffer_keeps_head_and_tail() -> None:
    buffer = BoundedBuffer(limit=10)
    for chunk in (b"HEAD-", b"middle-middle-", b"-TAIL"):
        buffer.write(chunk)
    assert buffer.truncated
    assert buffer.total_bytes == 24
    text = buffer.text()
    assert text.startswith("HEAD-")
    assert text.endswith("-TAIL")
    assert "14 bytes of output omitted" in text


def test_bounded_buffer_small_output_untouched() -> None:
    buffer = BoundedBuffer(limit=100)
    buffer.write(b"hello ")
    buffer.write(b"world")
    assert not buffer.truncated
    assert buffer.text() == "hello world"
