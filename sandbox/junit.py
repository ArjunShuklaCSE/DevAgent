"""Parse JUnit XML (as written by ``pytest --junitxml``) into typed results.

The XML is produced inside the sandbox from repository code, so it is untrusted:
``defusedxml`` rejects entity expansion and external entities, the file size is capped,
and every message is truncated.
"""

from pathlib import Path
from typing import Literal
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException, ElementTree
from pydantic import BaseModel, ConfigDict

MAX_REPORT_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_CHARS = 4000

Outcome = Literal["passed", "failed", "error", "skipped"]


class JUnitError(Exception):
    """The report is missing, too large or not valid JUnit XML."""


class TestCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    __test__ = False  # not a pytest test class

    test_id: str
    outcome: Outcome
    duration_seconds: float
    message: str | None = None


class TestReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    __test__ = False

    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_seconds: float
    cases: list[TestCaseResult]

    @property
    def succeeded(self) -> bool:
        return self.failed == 0 and self.errors == 0 and self.total > 0

    def failing(self) -> list[TestCaseResult]:
        return [c for c in self.cases if c.outcome in ("failed", "error")]


def parse_junit_file(path: Path) -> TestReport:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise JUnitError(f"no JUnit report at {path.name}") from exc
    if size > MAX_REPORT_BYTES:
        raise JUnitError(f"JUnit report is too large ({size} bytes)")
    return parse_junit(path.read_bytes())


def parse_junit(data: bytes) -> TestReport:
    try:
        root = ElementTree.fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise JUnitError(f"invalid JUnit XML: {type(exc).__name__}") from exc
    if root.tag not in ("testsuites", "testsuite"):
        raise JUnitError(f"unexpected root element <{root.tag}>")

    cases = [_case(el) for el in root.iter("testcase")]
    counts = {o: sum(1 for c in cases if c.outcome == o) for o in ("passed", "failed", "error")}
    return TestReport(
        total=len(cases),
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["error"],
        skipped=sum(1 for c in cases if c.outcome == "skipped"),
        duration_seconds=round(sum(c.duration_seconds for c in cases), 3),
        cases=cases,
    )


def _case(element: Element) -> TestCaseResult:
    classname = element.get("classname", "")
    name = element.get("name", "")
    test_id = f"{classname}::{name}" if classname else name
    outcome: Outcome = "passed"
    message: str | None = None
    for child, kind in (("error", "error"), ("failure", "failed"), ("skipped", "skipped")):
        found = element.find(child)
        if found is not None:
            outcome = kind  # type: ignore[assignment]
            text = found.get("message") or ""
            if found.text:
                text = f"{text}\n{found.text}" if text else found.text
            message = text[:MAX_MESSAGE_CHARS] or None
            break
    try:
        duration = float(element.get("time", "0") or 0)
    except ValueError:
        duration = 0.0
    return TestCaseResult(
        test_id=test_id[:500], outcome=outcome, duration_seconds=duration, message=message
    )
