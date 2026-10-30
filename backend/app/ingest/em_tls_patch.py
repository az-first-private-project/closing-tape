"""东财 TLS 指纹补丁（任务书 T2）。

背景：push2.eastmoney.com 等东财接口会重置 Python requests 的 TLS 指纹
（实测 RemoteDisconnected），但对 curl_cffi 伪装的 Chrome 指纹放行（实测 200）。

做法：monkeypatch ``requests.Session.request``——仅当 URL 含 eastmoney 域名时
改走 curl_cffi（impersonate="chrome"），并把响应包装回 requests 兼容接口；
其余 URL 走原始 requests，行为不变。

用法：在调用 akshare 东财接口之前 ``import app.ingest.em_tls_patch`` 即可
（akshare_provider.py 顶部已引入）。幂等，重复 import 无副作用。
"""

from __future__ import annotations

import os

# 国内数据源直连：清掉可能继承的代理，走代理反而被重置
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "*")

import requests as _requests
from curl_cffi import requests as _cffi

_EM_HOST_KEYWORDS = ("eastmoney",)
_TIMEOUT_DEFAULT = 30


class _CffiResponseShim:
    """把 curl_cffi 响应包装成 akshare 代码会用到的 requests.Response 子集。"""

    def __init__(self, r):  # noqa: ANN001
        self._r = r
        self.status_code = r.status_code
        self.text = r.text
        self.content = r.content
        self.headers = dict(r.headers) if r.headers else {}
        self.url = str(r.url)
        self.encoding = getattr(r, "encoding", None) or "utf-8"

    def json(self, **kwargs):  # noqa: ANN003, ANN002
        return self._r.json(**kwargs)

    def raise_for_status(self):  # noqa: D102
        if self.status_code >= 400:
            import requests as _rq

            raise _rq.HTTPError(f"{self.status_code} for {self.url}")
        return self

    @property
    def ok(self) -> bool:  # noqa: D102
        return self.status_code < 400


_orig_request = _requests.Session.request
_patched = False


def _is_em_url(url: str | None) -> bool:
    if not url:
        return False
    return any(k in url for k in _EM_HOST_KEYWORDS)


def _patched_request(self, method, url, **kwargs):  # noqa: ANN001, ANN003
    if not _is_em_url(url):
        return _orig_request(self, method, url, **kwargs)
    kwargs.pop("allow_redirects", None)
    kwargs.pop("verify", None)
    kwargs.pop("cert", None)
    kwargs.pop("proxies", None)
    kwargs.pop("data", None)
    kwargs.setdefault("timeout", _TIMEOUT_DEFAULT)
    resp = _cffi.request(method, url, impersonate="chrome", **kwargs)
    return _CffiResponseShim(resp)


def apply() -> None:
    """安装补丁（幂等）。"""
    global _patched
    if _patched:
        return
    _requests.Session.request = _patched_request  # type: ignore[method-assign]
    _patched = True
    import logging

    logging.getLogger(__name__).info("em_tls_patch 已启用：eastmoney 请求走 curl_cffi(chrome)")


apply()
