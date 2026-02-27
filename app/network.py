from __future__ import annotations

import os
from typing import Callable, Dict

import requests
from typing import Dict


PROXY_ENV_KEYS = [
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
]


def get_proxy_env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key in PROXY_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            out[key] = val
    return out


def clear_proxy_env() -> Dict[str, str]:
    existing = get_proxy_env()
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    return existing


def force_no_proxy_all() -> None:
    # Force requests/urllib to bypass proxy resolution for all hosts.
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


_REQUESTS_PATCHED = False
_ORIG_SESSION_INIT: Callable | None = None


def disable_requests_env_proxy() -> None:
    """
    Force all future requests Session objects to ignore env/system proxies.
    """
    global _REQUESTS_PATCHED, _ORIG_SESSION_INIT
    if _REQUESTS_PATCHED:
        return
    _ORIG_SESSION_INIT = requests.sessions.Session.__init__

    def _patched_init(self, *args, **kwargs):
        _ORIG_SESSION_INIT(self, *args, **kwargs)
        self.trust_env = False

    requests.sessions.Session.__init__ = _patched_init
    _REQUESTS_PATCHED = True
