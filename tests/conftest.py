"""Top-level pytest configuration for the blemees tests.

Auto-skip ``requires_claude`` / ``requires_codex``-marked tests unless
the caller opts in via ``pytest -m requires_claude`` /
``-m requires_codex``. Matches spec §11.3: these run only against a
real, authenticated CLI for that backend.
"""

from __future__ import annotations

import pytest

_E2E_MARKERS = ("requires_claude", "requires_codex")


try:
    from _pytest.mark.expression import Expression as _MarkExpression

    def _expr_selects_only(markexpr: str, marker: str) -> bool:
        """Return True when the expression evaluates True for *only* the
        named marker — i.e. a deliberate opt-in to the gated suite."""
        return _MarkExpression.compile(markexpr).evaluate(lambda name: name == marker)

except Exception:  # pragma: no cover — old pytest / import failure

    def _expr_selects_only(markexpr: str, marker: str) -> bool:  # type: ignore[misc]
        return markexpr.strip() == marker


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    markexpr = config.getoption("-m") or ""
    selected = {m for m in _E2E_MARKERS if markexpr and _expr_selects_only(markexpr, m)}
    for item in items:
        for marker in _E2E_MARKERS:
            if marker in selected:
                continue
            if marker in item.keywords:
                item.add_marker(
                    pytest.mark.skip(
                        reason=(
                            f"requires real authenticated CLI for `{marker.split('_', 1)[1]}`; "
                            f"run with `-m {marker}`"
                        )
                    )
                )
