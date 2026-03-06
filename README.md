# A-share Daily Picker

Command-line tool to recommend top A-share stocks before open using T-1 close data.

中文说明：一个基于 T-1 收盘数据、用于开盘前选股的 A 股命令行研究工具。

## Commands

- `python3 -m app.main recommend --date YYYY-MM-DD [--count N]`
- `python3 -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal` normal relaxed force
- `python3 -m app.main backtest --start YYYY-MM-DD --end YYYY-MM-DD [--count N]`
- `python3 -m app.main doctor`
- `python3 -m app.main check-kline --symbol 000001 --start YYYY-MM-DD --end YYYY-MM-DD`
- `bash scripts/check_today_update.sh`
- `bash scripts/check_today_update_json.sh`
- `bash scripts/check_today_update_multi.sh`

## 中文命令说明

- `python3 -m app.main recommend --date YYYY-MM-DD [--count N]`：按指定日期推荐多只股票（默认 3 只，可通过 `--count` 覆盖）。
- `python3 -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal`：解释单只股票评分，`mode` 可选 `normal/relaxed/force`。
- `python3 -m app.main backtest --start YYYY-MM-DD --end YYYY-MM-DD [--count N]`：按历史区间回测策略表现（可指定每日选股数）。
- `python3 -m app.main doctor`：检查数据源连通性、DNS、HTTP 请求是否可用。
- `python3 -m app.main check-kline --symbol 000001 --start YYYY-MM-DD --end YYYY-MM-DD`：检查某只股票在区间内的 K 线拉取结果。
- `bash scripts/check_today_update.sh`：检查“今天交易日”数据是否已更新（表格输出）。
- `bash scripts/check_today_update_json.sh`：同上，JSON 输出（便于脚本集成）。
- `bash scripts/check_today_update_multi.sh`：使用多个探针股票联合检查（默认 `any` 规则）。

## Freshness Scripts

- `scripts/check_data_freshness.py`：通用检查脚本。参数：`--date YYYY-MM-DD`（默认今天），`--probe-symbol 000001`（可重复传参），`--require any|all`（多探针通过规则），`--output table|json`。
- 退出码：`0` 数据已更新；`2` 数据未更新；`1` 脚本执行异常。

## Notes

- This is a research tool, not investment advice.
- Default config: `config/default.yaml`.
- If your network has broken proxy variables, keep `network.disable_env_proxy: true` (default) to force direct requests.
- If macOS system proxy still interferes, keep `network.force_no_proxy_all: true` (default).
- `recommend` prints progress logs while scanning symbols; tune speed with `strategy.max_symbols_per_run` and `data_source.request_timeout_sec` (`0` means no limit).
- Default pick count is `strategy.pick_count` (currently `3`), and can be overridden by `recommend --count`.
- Mode chain is configurable via `strategy.enabled_modes`, e.g. `[normal]` or `[normal, relaxed, force]`.
- If no stock passes normal/relaxed rules, the engine auto-falls back to `force` mode. If `force` still has no candidate, the run stops with an error instead of returning a fallback pick.
- Runtime log shows `stocks_total / filtered / universe` to help verify whether you are scanning full market.
- Local cache is enabled by default at `.cache/akshare` to speed up repeated recommend/backtest runs.
- Scoring now includes a volume module (`vol_ratio_5_20`, `volume_zscore20`) in addition to trend/momentum/stability.
- Added market regime filter (CSI300 bull/neutral/bear) and stock-level risk filter (price/volatility/RSI/volume constraints).
- Backtest now reports gross/net returns with execution cost model (commission, stamp duty, slippage).
- Backtest uses an equal-weight basket based on `strategy.pick_count`, and supports temporary override via `backtest --count`.
- Recommend output includes suggested `stop_loss_price` and `take_profit_price` (ATR-based by default, configurable in `risk_targets`).
- Recommend output includes `suggested_holding_days` (rule-based from momentum/volatility/RSI + market regime).
- Each `recommend` run appends a row to `reports/recommendations.csv` (time, symbol, name, stop-loss, take-profit, etc.).
- Each `recommend` run also appends a Markdown table row to `reports/recommendations.md`.
- Each `recommend` run streams raw console output to signal-date log file in real time, default `reports/{signal_date}.log` (for example `reports/20260303.log`).
- Universe filter now supports excluding GEM board (`300*`) via `filters.exclude_gem_board`.
- Universe board filter excludes STAR (`688*`,`689*`) and BJ-related (`4*`,`8*`,`9*`, including `92*`) when enabled.
- Recommend prints a warning when `signal_date` bars are likely not updated yet (data freshness check).
- `market_filter.stop_on_stale: true` (default) stops `recommend` when index data date is older than `signal_date`.

## 中文注意事项

- 本项目用于策略研究，不构成投资建议。
- 默认配置文件：`config/default.yaml`。
- 若本机代理环境变量异常，保持 `network.disable_env_proxy: true`（默认）可强制直连。
- 若 macOS 系统代理仍干扰请求，保持 `network.force_no_proxy_all: true`（默认）。
- `recommend` 会输出扫描进度日志；可通过 `strategy.max_symbols_per_run` 和 `data_source.request_timeout_sec` 调整速度。
- 默认选股数量由 `strategy.pick_count` 控制（当前为 `3`），可通过 `recommend --count` 临时覆盖。
- 可通过 `strategy.enabled_modes` 配置启用模式链，例如 `[normal]` 或 `[normal, relaxed, force]`。
- 若 `normal/relaxed` 都无候选，程序会自动降级到 `force`；若 `force` 仍无候选则报错结束，不再返回兜底股票。
- 运行日志里的 `stocks_total / filtered / universe` 可帮助确认是否在全市场扫描。
- 默认启用本地缓存目录 `.cache/akshare`，重复运行 `recommend/backtest` 会更快。
- 回测输出包含毛收益/净收益，净收益已计入手续费、印花税、滑点等执行成本。
- 回测会按 `strategy.pick_count` 进行等权组合收益计算，也可通过 `backtest --count` 临时指定每日选股数。
- `recommend` 输出包含止损/止盈价（默认 ATR 方式，可在 `risk_targets` 配置）。
- 每次 `recommend` 会追加写入：`reports/recommendations.csv` 与 `reports/recommendations.md`。
- 每次 `recommend` 会实时把终端输出原样追加到信号日日志，默认 `reports/{signal_date}.log`（例如 `reports/20260303.log`）。
- 开启相关过滤时，板块过滤会排除科创 (`688*`,`689*`) 与北交相关 (`4*`,`8*`,`9*`，含 `92*`) 代码。
- 当 `signal_date` 数据可能未更新时，会给出数据新鲜度告警。
- `market_filter.stop_on_stale: true`（默认）会在指数数据日期落后于 `signal_date` 时直接停止推荐，避免用旧指数继续计算。

## 中文常见问题

- 回测报错“交易日不足”：`backtest` 至少需要 8 个交易日，需扩大 `--start/--end` 区间。
- 回测里 `normal candidates=0`：常见于熊市拦截、阈值过严或历史数据不完整；程序会尝试 `relaxed/force`，若仍无候选则跳过当日并记录原因。
- 终端出现 `NotOpenSSLWarning`：这是 Python/urllib3 环境告警，不是本项目逻辑错误。
