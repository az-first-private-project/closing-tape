from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill_sector_scores import _needs_compute, _needs_prune


def test_needs_compute_when_scored_rows_short() -> None:
    assert _needs_compute(465, 0, False) is True
    assert _needs_compute(465, 464, False) is True
    assert _needs_compute(465, 465, False) is False


def test_needs_compute_forced_even_when_complete() -> None:
    assert _needs_compute(465, 465, True) is True


def test_needs_prune_requires_explicit_flag() -> None:
    assert _needs_prune(10, False) is False
    assert _needs_prune(0, True) is False
    assert _needs_prune(10, True) is True
