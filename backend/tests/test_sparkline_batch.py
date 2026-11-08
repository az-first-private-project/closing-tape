"""批量走势端点（P2-5）的纯函数测试。"""
from __future__ import annotations

import pytest

from app.api.v1.stocks import _parse_batch_codes
from app.core.errors import InvalidParamError


def test_parse_batch_codes_zfills_and_dedupes() -> None:
    assert _parse_batch_codes("600519, 1,600519,000001") == [
        "600519", "000001",
    ]


@pytest.mark.parametrize("raw", ["", " , ", "600519,abc", "600519,1234567"])
def test_parse_batch_codes_rejects_invalid(raw: str) -> None:
    with pytest.raises(InvalidParamError):
        _parse_batch_codes(raw)


def test_parse_batch_codes_rejects_too_many() -> None:
    raw = ",".join(f"{i:06d}" for i in range(201))
    with pytest.raises(InvalidParamError):
        _parse_batch_codes(raw)
