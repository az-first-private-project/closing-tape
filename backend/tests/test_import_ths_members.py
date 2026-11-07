from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from import_ths_members import _better, is_complete  # noqa: E402


def _members(n: int) -> list[dict[str, str]]:
    return [{"stock_code": f"{i:06d}", "stock_name": ""} for i in range(n)]


def test_concept_complete_lower_bound() -> None:
    assert is_complete(_members(1), 1, "concept")
    assert not is_complete(_members(90), 10, "concept")
    assert is_complete(_members(91), 10, "concept")


def test_industry_page_size_is_20() -> None:
    assert not is_complete(_members(100), 6, "industry")
    assert is_complete(_members(101), 6, "industry")


def test_partial_pages_require_full_ten_pages() -> None:
    assert not is_complete(_members(99), 20, "concept")
    assert is_complete(_members(100), 20, "concept")
    assert not is_complete(_members(199), 20, "industry")
    assert is_complete(_members(200), 20, "industry")


def test_better_prefers_complete_over_partial() -> None:
    partial_full = ("A", _members(100), 20, "a.json")
    complete_small = ("A", _members(50), 5, "b.json")
    assert _better(complete_small, partial_full, "concept")
    assert not _better(partial_full, complete_small, "concept")


def test_better_prefers_more_members_for_same_completeness() -> None:
    small = ("A", _members(30), 3, "a.json")
    big = ("A", _members(55), 6, "b.json")
    assert _better(big, small, "concept")
