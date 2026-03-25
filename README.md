# A-share Daily Picker

基于 T-1 收盘数据、用于开盘前选股和回测的 A 股命令行研究工具。

当前建议把它当成一个以 `recommend-adaptive` 为主入口、以 `backtest-adaptive` 为主验证入口的本地研究仓库，而不是一个面向外部发布的通用产品。

## 当前主线

- 正式主入口：`recommend-adaptive`
- 正式主回测：`backtest-adaptive`
- 正式主配置：`config/default.yaml`
- 稳定快照：`config/default.stable.yaml`
- 历史基线：`config/default.baseline.yaml`
- 研究总结：`FINAL_STRATEGY_SUMMARY.md`

当前默认思路：

- `bull / neutral` 市场优先走 `recommend-pullback`
- `bear` 市场优先走 `recommend-oversold`
- 没有合格信号时允许空仓

## 跨平台命令约定

- macOS / Linux 默认使用 `python3`
- Windows 默认使用 `python`
- 看到 `python3 -m app.main ...` 时，Windows 直接替换为 `python -m app.main ...`
- 看到 `python3 scripts/xxx.py ...` 时，Windows 直接替换为 `python scripts/xxx.py ...`

## 快速开始

### 1. 安装依赖

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. 先跑一遍主入口

```bash
# macOS / Linux
python3 -m app.main recommend-adaptive --date YYYY-MM-DD

# Windows
python -m app.main recommend-adaptive --date YYYY-MM-DD
```

如果不传 `--date`，推荐类命令默认会尝试使用“下一个交易日”。

### 3. 跑主回测

```bash
# macOS / Linux
python3 -m app.main backtest-adaptive --start YYYY-MM-DD --end YYYY-MM-DD --output table

# Windows
python -m app.main backtest-adaptive --start YYYY-MM-DD --end YYYY-MM-DD --output table
```

### 4. 查看报表

- 推荐结果默认写到 `reports/`
- 仪表盘数据默认写到 `reports/dashboard-data.js`
- 静态页面入口是 `index.html`

## 日常使用建议

### 盘前正式信号

```bash
# macOS / Linux
python3 -m app.main recommend-adaptive --date YYYY-MM-DD

# Windows
python -m app.main recommend-adaptive --date YYYY-MM-DD
```

### 固定持有期回测

```bash
# macOS / Linux
python3 -m app.main backtest-adaptive --start YYYY-MM-DD --end YYYY-MM-DD --output table

# Windows
python -m app.main backtest-adaptive --start YYYY-MM-DD --end YYYY-MM-DD --output table
```

### 优化前后对比

```bash
# macOS / Linux
python3 scripts/compare_adaptive_configs.py --start YYYY-MM-DD --end YYYY-MM-DD --output table

# Windows
python scripts/compare_adaptive_configs.py --start YYYY-MM-DD --end YYYY-MM-DD --output table
```

### 空仓时看观察池

```bash
# macOS / Linux
python3 -m app.main recommend-opportunity --date YYYY-MM-DD

# Windows
python -m app.main recommend-opportunity --date YYYY-MM-DD
```

### 解释单只股票为什么入选或被过滤

```bash
# macOS / Linux
python3 -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal

# Windows
python -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal
```

## 命令总览

### 推荐类命令

| 命令 | 用途 | 说明 |
| --- | --- | --- |
| `recommend-adaptive` | 日常主入口 | 按市场状态自动选择策略 |
| `recommend-opportunity` | 观察池 | 给人工复核更宽的候选集 |
| `recommend` | 默认趋势策略 | 历史默认入口，现偏研究用途 |
| `recommend-pullback` | 回踩确认策略 | 当前主流程的核心策略 |
| `recommend-oversold` | 超跌反弹策略 | 弱市补充策略 |
| `recommend-all` | 顺序跑多套推荐 | 依次执行默认、回踩、超跌 |
| `recommend-bull` | 强市研究策略 | 研究用途，不建议接入主流程 |
| `recommend-relative` | 相对强度研究策略 | 研究用途，不建议接入主流程 |

推荐类命令的常见参数：

- `--date YYYY-MM-DD`
- `--count N`
- `--output table|json`

### 回测类命令

| 命令 | 用途 | 说明 |
| --- | --- | --- |
| `backtest-adaptive` | 主回测入口 | 验证当前正式流程 |
| `backtest-adaptive-rules` | 规则退出回测 | 第一版规则退出实验 |
| `backtest` | 默认趋势策略回测 | 对应 `recommend` |
| `backtest-pullback` | 回踩策略回测 | 对应 `recommend-pullback` |
| `backtest-bull` | 强市研究回测 | 对应 `recommend-bull` |
| `backtest-relative` | 相对强度研究回测 | 对应 `recommend-relative` |

回测类命令的常见参数：

- `--start YYYY-MM-DD`
- `--end YYYY-MM-DD`
- `--count N`
- `--output table|json|json-cn`

### 诊断与工具命令

| 命令 | 用途 |
| --- | --- |
| `explain` | 解释单只股票的评分、过滤和关键指标 |
| `doctor` | 检查网络、数据源连通性和基础环境 |
| `check-kline` | 检查单只股票在指定区间的 K 线抓取 |
| `check-sector-map` | 校验本地板块映射文件覆盖率 |
| `export-dashboard-data` | 生成 `index.html` 使用的汇总数据 |

### 新鲜度检查脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/check_today_update.sh` | 表格方式检查当日数据是否更新 |
| `scripts/check_today_update_json.sh` | JSON 方式检查当日数据是否更新 |
| `scripts/check_today_update_multi.sh` | 多探针股票联合检查 |
| `scripts/check_data_freshness.py` | 通用新鲜度检查脚本 |

`scripts/check_data_freshness.py` 常用参数：

- `--date YYYY-MM-DD`
- `--probe-symbol 000001`
- `--require any|all`
- `--output table|json`

退出码：

- `0`：数据已更新
- `2`：数据未更新
- `1`：脚本执行异常

## 常用命令示例

### 主推荐

```bash
# macOS / Linux
python3 -m app.main recommend-adaptive
python3 -m app.main recommend-adaptive --date 2026-03-23 --count 1
python3 -m app.main recommend-adaptive --date 2026-03-23 --output json

# Windows
python -m app.main recommend-adaptive
python -m app.main recommend-adaptive --date 2026-03-23 --count 1
python -m app.main recommend-adaptive --date 2026-03-23 --output json
```

### 主回测

```bash
# macOS / Linux
python3 -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --output table
python3 -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --output json-cn

# Windows
python -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --output table
python -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --output json-cn
```

### 研究对比

```bash
# macOS / Linux
python3 -m app.main backtest-pullback --start 2025-09-01 --end 2026-03-23 --output table
python3 -m app.main backtest-bull --start 2025-09-01 --end 2026-03-23 --output table
python3 -m app.main backtest-relative --start 2025-09-01 --end 2026-03-23 --output table

# Windows
python -m app.main backtest-pullback --start 2025-09-01 --end 2026-03-23 --output table
python -m app.main backtest-bull --start 2025-09-01 --end 2026-03-23 --output table
python -m app.main backtest-relative --start 2025-09-01 --end 2026-03-23 --output table
```

### 单点排查

```bash
# macOS / Linux
python3 -m app.main doctor
python3 -m app.main check-kline --symbol 000001 --start 2026-02-01 --end 2026-03-01
python3 -m app.main check-sector-map --path data/sector_map.csv

# Windows
python -m app.main doctor
python -m app.main check-kline --symbol 000001 --start 2026-02-01 --end 2026-03-01
python -m app.main check-sector-map --path data/sector_map.csv
```

## 输出文件

常见输出目录是 `reports/`。

推荐命令通常会写入：

- `reports/recommendations.csv`
- `reports/recommendations.md`
- `reports/recommendations.txt`
- `reports/pullback_recommendations.csv`
- `reports/oversold_recommendations.csv`
- `reports/opportunity_recommendations.csv`
- `reports/{signal_date}.log`
- `reports/dashboard-data.js`

自适应回测通常会写入：

- `reports/backtests/adaptive/{period_key}.json`
- `reports/backtests/adaptive/latest.json`
- `reports/backtests/adaptive_compare/{period_key}.txt`

其中：

- `index.html` 读取 `reports/dashboard-data.js`
- `reports/*.log` 属于运行日志，通常不建议提交
- `.cache/akshare` 是本地缓存目录，通常也不建议提交

## 配置说明

默认配置文件是 `config/default.yaml`，主要控制这些内容：

- 网络代理处理
- 数据源超时、重试与本地缓存
- 股票池与基础过滤器
- 市场状态过滤
- 个股风险过滤
- 评分逻辑与策略 profile
- 报表与日志输出
- 回测成本模型

常见相关配置文件：

- `config/default.yaml`：当前正式主配置
- `config/default.stable.yaml`：稳定快照
- `config/default.baseline.yaml`：历史基线
- `config/default.aggressive.yaml`：更激进版本
- `config/default.oversold-neutral.yaml`：偏超跌 / 中性环境变体
- `config/default.oversold-fallback.yaml`：超跌兜底变体

切换配置文件时：

```bash
# macOS / Linux
python3 -m app.main --config config/default.stable.yaml recommend-adaptive --date YYYY-MM-DD

# Windows
python -m app.main --config config/default.stable.yaml recommend-adaptive --date YYYY-MM-DD
```

## 板块映射

板块映射文件默认是 `data/sector_map.csv`，最小格式如下：

```csv
symbol,sector
000001,银行
600519,白酒
```

用途：

- 提供 `股票 -> 板块/行业` 映射
- 支持板块覆盖率检查
- 给相关研究策略提供板块上下文

检查方式：

```bash
# macOS / Linux
python3 -m app.main check-sector-map --path data/sector_map.csv

# Windows
python -m app.main check-sector-map --path data/sector_map.csv
```

补充说明见 `data/sector_map.README.md`。

## 目录结构

```text
app/                    核心代码
  backtest/             回测逻辑
  data_source/          数据源封装
  engine/               推荐引擎
  features/             技术指标
  strategy/             打分、持有期、风险目标
  universe/             股票池过滤
config/                 YAML 配置
data/                   本地静态数据
reports/                推荐结果、回测结果、仪表盘数据
scripts/                辅助脚本
tests/                  测试
index.html              静态仪表盘页面
FINAL_STRATEGY_SUMMARY.md  策略阶段性结论
```

## 注意事项

- 本项目用于策略研究，不构成投资建议。
- `recommend-*` 输出的是候选结果，不等于保证收益的交易信号。
- 如果数据源还没更新到目标 `signal_date`，程序可能告警或停止，取决于 `data_freshness` 和 `market_filter` 设置。
- 若网络环境代理混乱，通常建议保留：
  - `network.disable_env_proxy: true`
  - `network.force_no_proxy_all: true`
- 若 `normal` 模式候选为 0，可优先检查：
  - `strategy.enabled_modes`
  - `fallback.mode`
  - `risk_filter`
  - `market_filter`

## 常见问题

- 回测报错“交易日不足”：扩大 `--start` / `--end` 区间。
- 回测里出现 `normal candidates=0`：常见原因是熊市过滤、阈值过严、板块过滤过多或历史数据不完整。
- 终端出现 `NotOpenSSLWarning`：通常是 Python / urllib3 环境告警，不是项目核心逻辑错误。
- 运行后生成很多日志：这是 `reporting.recommendation_log` 在工作，默认会写到 `reports/{signal_date}.log`。
