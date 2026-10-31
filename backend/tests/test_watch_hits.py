"""自选股命中查询的纯校验测试（V6 T8）。

不依赖数据库：
  - 代码格式（1–6 位数字）、去重保序、上限 100、空列表；
  - days 越界由 API 的 Pydantic 约束负责，这里只测代码归一化。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

pytest.importorskip("sqlalchemy", reason="sqlalchemy 未安装，跳过自选命中测试")

from app.core.errors import InvalidParamError  # noqa: E402
from app.engine.watch_hits import (  # noqa: E402
    MAX_HITS_CODES,
    normalize_hits_codes,
)


def test_codes_are_deduped_and_order_preserved():
    assert normalize_hits_codes(["600000", "000001", "600000", "1"]) == [
        "600000",
        "000001",
        "1",
    ]
    assert normalize_hits_codes([]) == []


@pytest.mark.parametrize(
    "value",
    [
        "",
        "1234567",
        "60000a",
        "'; drop",
        " 600000",
        "600000 ",
        "-600000",
        "600000\n",
        None,
        600000,
        True,
    ],
)
def test_invalid_codes_are_rejected(value):
    with pytest.raises(InvalidParamError) as exc:
        normalize_hits_codes([value])
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_PARAM"


def test_code_limit_is_applied_after_dedup():
    codes = [f"{index:06d}" for index in range(MAX_HITS_CODES)]
    assert len(normalize_hits_codes(codes)) == MAX_HITS_CODES

    with pytest.raises(InvalidParamError):
        normalize_hits_codes(codes + ["999999"])

    # 重复项不占名额
    assert len(normalize_hits_codes(codes + [codes[0]] * 50)) == MAX_HITS_CODES
