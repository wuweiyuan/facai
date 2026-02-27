# A-share Daily Picker

Command-line tool to recommend one A-share stock before open using T-1 close data.

中文说明：一个基于 T-1 收盘数据、用于开盘前选股的 A 股命令行研究工具。

## Commands

- `python3 -m app.main recommend --date YYYY-MM-DD`
- `python3 -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal` normal relaxed force
- `python3 -m app.main backtest --start YYYY-MM-DD --end YYYY-MM-DD`
- `python3 -m app.main doctor`
- `python3 -m app.main check-kline --symbol 000001 --start YYYY-MM-DD --end YYYY-MM-DD`

## 中文命令说明

- `python3 -m app.main recommend --date YYYY-MM-DD`：按指定日期推荐 1 只股票。
- `python3 -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal`：解释单只股票评分，`mode` 可选 `normal/relaxed/force`。
- `python3 -m app.main backtest --start YYYY-MM-DD --end YYYY-MM-DD`：按历史区间回测策略表现。
- `python3 -m app.main doctor`：检查数据源连通性、DNS、HTTP 请求是否可用。
- `python3 -m app.main check-kline --symbol 000001 --start YYYY-MM-DD --end YYYY-MM-DD`：检查某只股票在区间内的 K 线拉取结果。

## Notes

- This is a research tool, not investment advice.
- Default config: `config/default.yaml`.
- If your network has broken proxy variables, keep `network.disable_env_proxy: true` (default) to force direct requests.
- If macOS system proxy still interferes, keep `network.force_no_proxy_all: true` (default).
- `recommend` prints progress logs while scanning symbols; tune speed with `strategy.max_symbols_per_run` and `data_source.request_timeout_sec` (`0` means no limit).
- Mode chain is configurable via `strategy.enabled_modes`, e.g. `[normal]` or `[normal, relaxed, force]`.
- If no stock passes normal/relaxed rules, the engine auto-falls back to `force` mode. If `force` still has no candidate, the run stops with an error instead of returning a fallback pick.
- Runtime log shows `stocks_total / filtered / universe` to help verify whether you are scanning full market.
- Local cache is enabled by default at `.cache/akshare` to speed up repeated recommend/backtest runs.
- Scoring now includes a volume module (`vol_ratio_5_20`, `volume_zscore20`) in addition to trend/momentum/stability.
- Added market regime filter (CSI300 bull/neutral/bear) and stock-level risk filter (price/volatility/RSI/volume constraints).
- Backtest now reports gross/net returns with execution cost model (commission, stamp duty, slippage).
- Recommend output includes suggested `stop_loss_price` and `take_profit_price` (ATR-based by default, configurable in `risk_targets`).
- Recommend output includes `suggested_holding_days` (rule-based from momentum/volatility/RSI + market regime).
- Each `recommend` run appends a row to `reports/recommendations.csv` (time, symbol, name, stop-loss, take-profit, etc.).
- Each `recommend` run also appends a Markdown table row to `reports/recommendations.md`.
- Universe filter now supports excluding GEM board (`300*`) via `filters.exclude_gem_board`.
- Recommend prints a warning when `signal_date` bars are likely not updated yet (data freshness check).

## 中文注意事项

- 本项目用于策略研究，不构成投资建议。
- 默认配置文件：`config/default.yaml`。
- 若本机代理环境变量异常，保持 `network.disable_env_proxy: true`（默认）可强制直连。
- 若 macOS 系统代理仍干扰请求，保持 `network.force_no_proxy_all: true`（默认）。
- `recommend` 会输出扫描进度日志；可通过 `strategy.max_symbols_per_run` 和 `data_source.request_timeout_sec` 调整速度。
- 可通过 `strategy.enabled_modes` 配置启用模式链，例如 `[normal]` 或 `[normal, relaxed, force]`。
- 若 `normal/relaxed` 都无候选，程序会自动降级到 `force`；若 `force` 仍无候选则报错结束，不再返回兜底股票。
- 运行日志里的 `stocks_total / filtered / universe` 可帮助确认是否在全市场扫描。
- 默认启用本地缓存目录 `.cache/akshare`，重复运行 `recommend/backtest` 会更快。
- 回测输出包含毛收益/净收益，净收益已计入手续费、印花税、滑点等执行成本。
- `recommend` 输出包含止损/止盈价（默认 ATR 方式，可在 `risk_targets` 配置）。
- 每次 `recommend` 会追加写入：`reports/recommendations.csv` 与 `reports/recommendations.md`。
- 当 `signal_date` 数据可能未更新时，会给出数据新鲜度告警。

## 中文常见问题

- 回测报错“交易日不足”：`backtest` 至少需要 8 个交易日，需扩大 `--start/--end` 区间。
- 回测里 `normal candidates=0`：常见于熊市拦截、阈值过严或历史数据不完整；程序会尝试 `relaxed/force`，若仍无候选则跳过当日并记录原因。
- 终端出现 `NotOpenSSLWarning`：这是 Python/urllib3 环境告警，不是本项目逻辑错误。
