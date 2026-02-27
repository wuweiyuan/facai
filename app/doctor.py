from __future__ import annotations

import platform
import socket
import sys
from datetime import datetime
from typing import Any

import requests
from app.error_messages import friendly_error_message
from app.network import get_proxy_env

try:
    import akshare as ak
except ImportError as exc:  # pragma: no cover
    ak = None
    _AK_ERROR = exc
else:
    _AK_ERROR = None


def _dns_check(host: str) -> dict[str, Any]:
    try:
        ip = socket.gethostbyname(host)
        return {"ok": True, "host": host, "ip": ip}
    except Exception as exc:
        return {"ok": False, "host": host, "error": f"{type(exc).__name__}: {exc}"}


def _http_check(url: str, timeout_sec: int = 10) -> dict[str, Any]:
    try:
        resp = requests.get(url, timeout=timeout_sec)
        return {"ok": True, "url": url, "status_code": resp.status_code}
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}


def _akshare_trade_date_check() -> dict[str, Any]:
    if ak is None:
        return {"ok": False, "check": "akshare_trade_dates", "error": f"ImportError: {_AK_ERROR}"}
    try:
        df = ak.tool_trade_date_hist_sina()
        return {
            "ok": not df.empty,
            "check": "akshare_trade_dates",
            "rows": int(len(df)),
        }
    except Exception as exc:
        return {"ok": False, "check": "akshare_trade_dates", "error": f"{type(exc).__name__}: {exc}"}


def _akshare_spot_check() -> dict[str, Any]:
    if ak is None:
        return {"ok": False, "check": "akshare_spot", "error": f"ImportError: {_AK_ERROR}"}
    try:
        df = ak.stock_zh_a_spot_em()
        sample = []
        if not df.empty:
            cols = [c for c in ["代码", "名称"] if c in df.columns]
            if cols:
                sample = df.loc[:, cols].head(3).to_dict(orient="records")
        return {
            "ok": not df.empty,
            "check": "akshare_spot",
            "rows": int(len(df)),
            "sample": sample,
        }
    except Exception as exc:
        return {"ok": False, "check": "akshare_spot", "error": f"{type(exc).__name__}: {exc}"}


def run_doctor() -> dict[str, Any]:
    proxy_env = get_proxy_env()
    requests_proxy_view = {
        "finance.sina.com.cn": requests.utils.get_environ_proxies("https://finance.sina.com.cn"),
        "82.push2.eastmoney.com": requests.utils.get_environ_proxies("https://82.push2.eastmoney.com"),
    }
    checks = [
        {"required": True, **_dns_check("finance.sina.com.cn")},
        {"required": True, **_dns_check("82.push2.eastmoney.com")},
        {"required": True, **_http_check("https://finance.sina.com.cn")},
        {"required": True, **_http_check("https://82.push2.eastmoney.com")},
        {"required": True, **_akshare_trade_date_check()},
        {"required": False, **_akshare_spot_check()},
    ]
    for item in checks:
        if not item.get("ok") and item.get("error"):
            item["error_cn"] = friendly_error_message(str(item["error"]))
    required_checks = {
        "finance.sina.com.cn",
        "82.push2.eastmoney.com",
        "https://finance.sina.com.cn",
        "https://82.push2.eastmoney.com",
        "akshare_trade_dates",
    }
    all_ok = True
    for item in checks:
        key = item.get("check") or item.get("host") or item.get("url")
        if key in required_checks and not item.get("ok"):
            all_ok = False
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "proxy_env": proxy_env,
        "requests_proxy_view": requests_proxy_view,
        "all_ok": all_ok,
        "checks": checks,
    }


def print_doctor_report(report: dict[str, Any]) -> None:
    print(f"时间: {report['timestamp']}")
    print(f"系统: {report['platform']}")
    print(f"Python: {report['python']}")
    proxy_env = report.get("proxy_env", {})
    if proxy_env:
        print(f"代理环境变量: {proxy_env}")
    else:
        print("代理环境变量: 未检测到")
    print(f"requests识别代理: {report.get('requests_proxy_view', {})}")
    print(f"总体状态: {'PASS' if report['all_ok'] else 'FAIL'}")
    if report["all_ok"]:
        print("结论: 主链路可用，可直接运行 recommend；可选检查失败不会阻断推荐。")
    print("")
    for idx, c in enumerate(report["checks"], start=1):
        if c.get("ok"):
            status = "PASS"
        else:
            status = "FAIL" if c.get("required", True) else "WARN"
        title = c.get("check") or c.get("host") or c.get("url") or f"check_{idx}"
        print(f"{idx}. [{status}] {title}")
        if "ip" in c:
            print(f"   ip: {c['ip']}")
        if "status_code" in c:
            print(f"   status_code: {c['status_code']}")
        if "rows" in c:
            print(f"   rows: {c['rows']}")
        if "sample" in c and c["sample"]:
            print(f"   sample: {c['sample']}")
        if "error" in c:
            print(f"   error: {c.get('error_cn', c['error'])}")
