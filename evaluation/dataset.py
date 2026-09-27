"""Benchmark dataset format (spec 13.1) and loader.

Paths inside a dataset (hidden tests, gold patches) are relative to a directory named
after the dataset next to its YAML file, e.g. ``datasets/starter/``. The agent never
sees any of it: hidden tests are written into a fresh copy of the agent's result only
when scoring.
"""

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Difficulty = Literal["easy", "medium", "hard"]
_SAMPLE_REPO = re.compile(r"^sample:[a-z][a-z0-9_-]{0,63}$")
_NODE_ID = re.compile(r"^[\w./-]+\.py(::[\w\[\]-]+)+$")


class DatasetError(Exception):
    """The dataset file is missing, malformed, or refers to files that do not exist."""


def junit_id(node_id: str) -> str:
    """pytest node id -> the id pytest writes to JUnit XML (``classname::name``).

    ``tests/test_x.py::TestA::test_b`` -> ``tests.test_x.TestA::test_b``.
    """
    path, *parts = node_id.split("::")
    module = path.removesuffix(".py").replace("/", ".")
    return f"{'.'.join([module, *parts[:-1]])}::{parts[-1]}"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,80}$")
    repo: str = Field(description="sample:<name>: a bundled sample repository (v1 only)")
    base_commit: str = Field(description="commit SHA, or 'sample' for bundled samples")
    language: str
    framework: str
    difficulty: Difficulty
    issue_title: str = Field(min_length=1)
    issue_body: str = ""
    issue_number: int | None = None
    hidden_tests: dict[str, str] = Field(min_length=1, description="repo path -> dataset file")
    fail_to_pass: list[str] = Field(min_length=1)
    pass_to_pass: list[str] = Field(default_factory=list)
    gold_patch: str | None = None
    cassette: str | None = Field(default=None, description="ScriptedLLM cassette, if recorded")
    adversarial: bool = False
    notes: str = ""

    @field_validator("fail_to_pass", "pass_to_pass")
    @classmethod
    def _node_ids(cls, value: list[str]) -> list[str]:
        for node_id in value:
            if not _NODE_ID.match(node_id):
                raise ValueError(f"not a pytest node id: {node_id!r}")
        return value

    @field_validator("repo")
    @classmethod
    def _sample_repo(cls, value: str) -> str:
        if not _SAMPLE_REPO.match(value):
            raise ValueError(f"repo must be sample:<name> (a bundled sample), got {value!r}")
        return value

    @field_validator("hidden_tests")
    @classmethod
    def _hidden_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for target in value:
            if target.startswith("/") or ".." in Path(target).parts:
                raise ValueError(f"hidden test target must be a relative repo path: {target!r}")
        return value

    @model_validator(mode="after")
    def _f2p_in_hidden_tests(self) -> "Case":
        hidden = set(self.hidden_tests)
        for node_id in self.fail_to_pass:
            if node_id.split("::")[0] not in hidden:
                raise ValueError(f"{node_id} is not in one of the hidden test files")
        return self

    @property
    def sample(self) -> str | None:
        return self.repo.removeprefix("sample:") if self.repo.startswith("sample:") else None

    @model_validator(mode="after")
    def _sample_base(self) -> "Case":
        if self.base_commit != "sample":
            raise ValueError("sample:<name> repositories use base_commit: sample")
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    version: str
    description: str = ""
    cases: list[Case] = Field(min_length=1)

    # Set by ``load_dataset``, not part of the file.
    path: Path = Field(default=Path(), exclude=True)
    content_sha256: str = Field(default="", exclude=True)

    @model_validator(mode="after")
    def _unique_ids(self) -> "Dataset":
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self

    @property
    def files_dir(self) -> Path:
        return self.path.parent / self.name

    def file(self, relative: str) -> Path:
        path = (self.files_dir / relative).resolve()
        if not path.is_relative_to(self.files_dir.resolve()):
            raise DatasetError(f"{relative} escapes the dataset directory")
        return path

    def case(self, case_id: str) -> Case:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise DatasetError(f"no case {case_id!r} in dataset {self.name}")


def load_dataset(path: Path) -> Dataset:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DatasetError(f"cannot read dataset {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
        dataset = Dataset.model_validate(data)
    except (yaml.YAMLError, ValueError) as exc:
        raise DatasetError(f"invalid dataset {path}: {exc}") from exc
    dataset = dataset.model_copy(
        update={"path": path.resolve(), "content_sha256": hashlib.sha256(raw).hexdigest()}
    )
    for case in dataset.cases:
        for source in case.hidden_tests.values():
            if not dataset.file(source).is_file():
                raise DatasetError(f"{case.id}: hidden test file {source} is missing")
        if case.gold_patch and not dataset.file(case.gold_patch).is_file():
            raise DatasetError(f"{case.id}: gold patch {case.gold_patch} is missing")
    return dataset
