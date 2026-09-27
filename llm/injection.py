"""Heuristic prompt-injection flags (spec 7.4).

This is a warning signal for the UI, not a control: the controls are the capability
limits (no network, no push, no secrets, workspace-only tools) and the wrapping in
``llm.prompting``. Patterns are deliberately broad; a flag means "a human should look".
"""

import re
from dataclasses import dataclass

_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
    (pattern_id, label, re.compile(regex, re.IGNORECASE))
    for pattern_id, label, regex in (
        (
            "override_instructions",
            "asks the model to ignore or replace its instructions",
            r"\b(ignore|disregard|forget|override)\b[^.]{0,40}"
            r"\b(previous|prior|above|all|your|system)\b[^.]{0,20}"
            r"\b(instructions?|prompts?|rules?|guidelines)\b",
        ),
        (
            "role_hijack",
            "tries to redefine the model's role",
            r"\b(you are now|act as|new instructions|system prompt|developer mode)\b",
        ),
        (
            "exfiltrate_secrets",
            "asks to reveal environment variables or secrets",
            r"(print|dump|echo|send|upload|post|exfiltrat\w*|leak|reveal)[^.]{0,50}"
            r"(env(ironment)?\s*var|os\.environ|printenv|secrets?"
            r"|api[_ -]?keys?|tokens?|credentials)",
        ),
        (
            "edit_ci",
            "asks to change CI configuration",
            r"(edit|modify|change|update|add|delete|remove|disable)[^.]{0,40}"
            r"(\.github/workflows|ci\.ya?ml|ci config|pipeline|github actions)",
        ),
        (
            "add_dependency",
            "asks to add a dependency or install a package",
            r"\b(pip install|add (a |the )?(new )?dependenc(y|ies)"
            r"|requirements\.txt|npm install)\b",
        ),
        (
            "network_call",
            "asks for a network call to an external host",
            r"\b(curl|wget|requests\.(get|post)|urllib\.request|http\.client|fetch\()"
            r"\s*[^\n]{0,80}https?://",
        ),
        (
            "change_remote",
            "asks to change git remotes or push",
            r"\bgit\s+(remote|push)\b|\bpush (to|the changes to)\b",
        ),
    )
)


@dataclass(frozen=True)
class InjectionFlag:
    pattern_id: str
    label: str
    source: str
    line: int
    excerpt: str


def scan(text: str, source: str, max_flags: int = 20) -> list[InjectionFlag]:
    """Flag suspicious passages. Matches may span line breaks within one sentence."""
    flags: list[InjectionFlag] = []
    for pattern_id, label, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            start = max(0, match.start() - 20)
            excerpt = " ".join(text[start : match.end() + 20].split())[:160]
            flags.append(InjectionFlag(pattern_id, label, source, line, excerpt))
            if len(flags) >= max_flags:
                return sorted(flags, key=lambda f: f.line)
    return sorted(flags, key=lambda f: f.line)
