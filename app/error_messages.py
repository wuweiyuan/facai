from __future__ import annotations

import re


def friendly_error_message(exc_or_msg: Exception | str) -> str:
    msg = str(exc_or_msg)

    if "does not match format" in msg:
        return "日期格式错误，请使用 YYYY-MM-DD，例如 2026-02-26。"
    if "Not enough trade dates for backtest" in msg:
        return "回测区间内交易日不足，至少需要 8 个交易日。请扩大 --start/--end 区间后重试。"
    if "No enough trade dates to resolve T-1 signal date" in msg:
        return "目标日期附近交易日不足，无法确定信号日(T-1)。请扩大日期范围后重试。"
    if "does not pass" in msg and "threshold" in msg:
        return "该股票未通过当前模式的选股阈值筛选。可尝试使用 --mode relaxed 或 --mode force。"
    if "does not pass risk filter" in msg:
        return "该股票未通过风险过滤规则，请更换标的或使用更宽松模式重试。"
    if "No bars found for" in msg:
        m = re.search(r"No bars found for\s+([0-9A-Za-z]+)", msg)
        if m:
            return f"未查询到股票 {m.group(1)} 在目标日期附近的K线数据。"
        return "未查询到该股票在目标日期附近的K线数据，请检查股票代码和日期。"
    if "No candidate found in enabled modes:" in msg:
        return "当前启用模式下无候选，已按配置停止返回结果。建议检查数据源连通性和过滤条件。"
    if "Failed to fetch stock universe from both name and spot sources" in msg:
        return "获取股票列表失败（名称接口和实时行情接口都不可用）。请稍后重试或先运行 doctor。"
    if "Failed to fetch daily bars from both EM/TX for" in msg:
        m = re.search(r"for\s+([0-9A-Za-z]+)", msg)
        if m:
            return f"获取股票 {m.group(1)} 日线失败（EM/TX 两个来源都不可用）。"
        return "获取股票日线失败（EM/TX 两个来源都不可用）。"
    if "Failed to fetch daily bars(EM) for" in msg:
        m = re.search(r"for\s+([0-9A-Za-z]+)", msg)
        if m:
            return f"获取股票 {m.group(1)} 日线失败（EM 数据源不可用）。"
        return "获取股票日线失败（EM 数据源不可用）。"
    if "Failed to fetch daily bars(TX) for" in msg:
        m = re.search(r"for\s+([0-9A-Za-z]+)", msg)
        if m:
            return f"获取股票 {m.group(1)} 日线失败（TX 数据源不可用）。"
        return "获取股票日线失败（TX 数据源不可用）。"
    if "Unsupported command:" in msg:
        return "命令不受支持，请检查子命令名称（recommend/explain/backtest/doctor/check-kline）。"
    if "Config not found:" in msg:
        return "配置文件不存在，请检查 --config 路径。"
    if "Config root must be an object" in msg:
        return "配置文件格式错误：根节点必须是对象（YAML 映射）。"
    if "Unsupported mode:" in msg:
        return "模式参数不支持，请使用 normal、relaxed 或 force。"
    if "akshare is required but unavailable" in msg:
        return "缺少 akshare 依赖或导入失败，请先安装项目依赖。"

    lower = msg.lower()
    if "name or service not known" in lower or "nodename nor servname provided" in lower:
        return "域名解析失败，请检查网络/DNS。"
    if "connection refused" in lower:
        return "连接被拒绝，请检查目标服务是否可访问。"
    if "read timed out" in lower or "connect timeout" in lower or "timed out" in lower:
        return "请求超时，请稍后重试或检查网络。"
    if "max retries exceeded" in lower:
        return "请求重试次数已用尽，网络或目标站点可能不可用。"
    if "ssl" in lower and "error" in lower:
        return "SSL 连接失败，请检查本机证书或网络中间代理。"

    return msg
