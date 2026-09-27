"""Static repository analyzer: picks the adapter for the dominant supported language."""

from collections.abc import Sequence
from pathlib import Path

from agent.analysis.adapters import DEFAULT_ADAPTERS, LanguageAdapter
from agent.analysis.model import RepoProfile
from agent.analysis.scan import RepoTree


class StaticRepositoryAnalyzer:
    def __init__(self, adapters: Sequence[LanguageAdapter] = DEFAULT_ADAPTERS) -> None:
        self._adapters = tuple(adapters)

    def analyze(self, root: Path) -> RepoProfile:
        tree = RepoTree.scan(root)
        ranked = [lang for lang, _ in tree.language_counts.most_common()]
        for language in ranked:
            for adapter in self._adapters:
                if language in adapter.languages and adapter.applies(tree):
                    return adapter.analyze(tree)
        return RepoProfile(
            languages=dict(tree.language_counts),
            primary_language=ranked[0] if ranked else None,
            adapter=None,
            supported=False,
            unsupported_reason="no language adapter for this repository"
            if ranked
            else "no source files found",
            framework=None,
            package_manager=None,
            dependency_files=[],
            test_framework=None,
            install_commands=[],
            test_command=None,
            lint_command=None,
            format_check_command=None,
            typecheck_command=None,
            entry_points=[],
            key_directories=tree.top_level_dirs()[:10],
            test_directories=[],
            evidence=[],
            confidence="low",
        )
