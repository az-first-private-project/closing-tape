"""NO_PROXY 中的方括号 IPv6 不能让 OpenAI/httpx 初始化失败。"""
from __future__ import annotations

import os

import app.services.llm_service as llm

DIRTY = "localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1"
CLEAN = "localhost,.localhost,127.0.0.1,127.0.0.0/8,::1"


def test_clean_no_proxy_strips_brackets_and_dedupes() -> None:
    assert llm._clean_no_proxy(DIRTY) == CLEAN
    assert llm._clean_no_proxy(" , [::1] ,, ::1 , ") == "::1"
    assert llm._clean_no_proxy("localhost,[not-ipv6]") == "localhost,[not-ipv6]"


def test_sanitize_proxy_env_updates_both_cases(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", DIRTY)
    monkeypatch.setenv("no_proxy", DIRTY)

    assert llm.sanitize_proxy_env() is True
    assert os.environ["NO_PROXY"] == CLEAN
    assert os.environ["no_proxy"] == CLEAN
    assert llm.sanitize_proxy_env() is False


def test_get_client_survives_dirty_no_proxy(monkeypatch) -> None:
    monkeypatch.setattr(llm, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setenv("NO_PROXY", DIRTY)
    monkeypatch.setenv("no_proxy", DIRTY)

    client = llm.get_client()
    try:
        assert client is not None
        assert os.environ["NO_PROXY"] == CLEAN
    finally:
        llm._client = None
        if client is not None:
            client.close()
