"""列表分页元信息（C1 追加式契约）。

只生成可选的 ``total / has_more / page / page_size`` 字段，调用方继续使用
现有 ``limit``（必要时补 ``offset``），不改变任何成功响应包裹形状。
"""
from __future__ import annotations


def pagination_meta(*, total: int, limit: int, offset: int, returned: int) -> dict:
    """按「全量命中 + 本页返回」计算分页元信息。

    ``page`` 从 1 开始；``limit`` 为页大小。``has_more`` 以实际返回条数为准，
    避免调用方把 limit 当成承诺条数。
    """
    page_size = max(1, int(limit))
    safe_offset = max(0, int(offset))
    return {
        "total": max(0, int(total)),
        "has_more": safe_offset + max(0, int(returned)) < max(0, int(total)),
        "page": safe_offset // page_size + 1,
        "page_size": page_size,
    }


__all__ = ["pagination_meta"]
