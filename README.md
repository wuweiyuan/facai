# A-share Daily Picker

基于 T-1 收盘数据、用于开盘前选股和回测的 A 股命令行研究工具。

当前建议把它当成一个以 `recommend-adaptive` 为主入口、以 `backtest-adaptive` 为主验证入口的本地研究仓库，而不是一个面向外部发布的通用产品。

## 当前主线

- 正式主入口：`recommend-adaptive`
- 正式主回测：`backtest-adaptive`
- 正式主配置：`config/default.yaml`
- 稳健快照：`config/default.stable-v2.yaml`
- 历史基线：`config/default.baseline.yaml`
- 研究总结：`FINAL_STRATEGY_SUMMARY.md`

当前默认思路：

- `bull` 市场优先走 `recommend-pullback`，再看 `recommend`
- `neutral / unknown` 市场直接空仓
- `bear` 市场只先尝试 `recommend-oversold`，没有合格信号就空仓
- 强市判定更严格：指数需明确站上 MA20，且 20 日动量足够强

## 收益与持有期口径

以下内容用于帮助理解当前主回测数字，不构成收益承诺。

### 收益率怎么理解

以当前最新主回测 `2025-03-24 -> 2026-05-27` 为例：

- 实际成交：`50`
- 空仓/跳过交易日：`230`
- `1日净胜率`：`56.00%`
- `3日净胜率`：`56.00%`
- 平均 `1日净收益`：`0.65%`
- 平均 `3日净收益`：`1.57%`
- 平均 `5日净收益`：`1.32%`
- `最大回撤代理`：`9.09%`

如果用 `4 万元`本金、按当前策略信号滚动执行，粗略可理解为：

- 单次信号按 `3日净收益` 均值估算：`40000 * 1.57% ≈ 628 元`
- 单次信号按 `5日净收益` 均值估算：`40000 * 1.32% ≈ 528 元`
- 若 50 次信号都用满 `4 万元`并接近回测均值，`3日口径`简单累计约 `3.14 万元`
- 若按 `5日口径`简单累计，约 `2.64 万元`

注意：

- 这里的数字是基于单笔平均净收益做的近似换算，不是完整实盘资金曲线，也不是收益承诺
- 实际结果会受仓位、滑点、买卖点、是否满仓、连续亏损、空仓天数影响
- `最大回撤代理 9.09%` 意味着 `4 万元`本金中途可能出现约 `3600 元`级别回撤
- 现在策略比旧版更少出手，收益主要来自少数强环境信号，不适合每天都强行交易

### 持有 `3` 天 / `5` 天怎么数

- 只按`交易日`计算，不按自然日计算
- 买入当天不算第 `1` 天
- `持有 3 天`：买入日后的第 `3` 个交易日卖出
- `持有 5 天`：买入日后的第 `5` 个交易日卖出
- 周末和节假日跳过不算

例如 `2026-03-23`（周一）买入：

| 交易日序号 | 日期 | 说明 |
| --- | --- | --- |
| 买入当日 | `2026-03-23` | 选出并买入 |
| 后 1 个交易日 | `2026-03-24` | 持有中 |
| 后 2 个交易日 | `2026-03-25` | 持有中 |
| 后 3 个交易日 | `2026-03-26` | `3 天持有`卖出日 |
| 后 4 个交易日 | `2026-03-27` | 持有中 |
| 后 5 个交易日 | `2026-03-30` | `5 天持有`卖出日 |

按工作周快速记：

| 买入日 | `3 天持有`卖出日 | `5 天持有`卖出日 |
| --- | --- | --- |
| 周一买 | 周四卖 | 下周一卖 |
| 周二买 | 周五卖 | 下周二卖 |
| 周三买 | 下周一卖 | 下周三卖 |
| 周四买 | 下周二卖 | 下周四卖 |
| 周五买 | 下周三卖 | 下周五卖 |

## 跨平台命令约定

- macOS / Linux 默认使用 `python3`
- Windows 默认使用 `python`
- 看到 `python3 -m app.main ...` 时，Windows 直接替换为 `python -m app.main ...`
- 看到 `python3 scripts/xxx.py ...` 时，Windows 直接替换为 `python scripts/xxx.py ...`

## 快速开始

如果你要在多台电脑之间共享股票缓存数据，先看 [OneDrive 股票缓存设置](docs/onedrive-cache-setup.md)。代码用 Git 同步，`.cache/akshare` 的真实数据用 OneDrive 同步。

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

### 尾盘人工候选

尾盘功能是独立命令，不接入 `recommend-adaptive`，也不写入现有推荐报表：

```bash
# macOS / Linux
python3 -m app.main tail-pick --date YYYY-MM-DD

# Windows
python -m app.main tail-pick --date YYYY-MM-DD
```

它需要盘中或近实时行情源。没有候选时会明确提示空仓。

### 尾盘自动运行

macOS 可以安装用户级 `launchd` 定时任务，工作日 `14:44` 自动运行尾盘策略：

```bash
cd /Users/wayne/data/myslef/发财/股票
scripts/install_tail_pick_launchd.sh
```

自动运行结果写到独立目录，不写现有推荐报表：

```bash
reports/tail_pick/YYYY-MM-DD.log
reports/tail_pick/latest.log
```

自动任务跑完后会弹出 macOS 通知，并自动打开 `reports/tail_pick/latest.log`。如果运行失败，也会打开同一个文件显示错误原因。

查看最近一次结果：

```bash
cd /Users/wayne/data/myslef/发财/股票
tail -n 80 reports/tail_pick/latest.log
```

停止自动运行：

```bash
cd /Users/wayne/data/myslef/发财/股票
scripts/uninstall_tail_pick_launchd.sh
```

重新安装自动运行：

```bash
cd /Users/wayne/data/myslef/发财/股票
scripts/install_tail_pick_launchd.sh
```

手动跑一次尾盘：

```bash
cd /Users/wayne/data/myslef/发财/股票
python3 -m app.main tail-pick
```

确认自动任务是否还在：

```bash
launchctl list com.wayne.tail-pick
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
| `tail-pick` | 尾盘人工候选 | 独立盘中命令，不接入主流程，不写现有推荐报表 |

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
- `--entry-price close|next-open`
  - `close`：信号日收盘买入，保持当前默认口径
  - `next-open`：信号次日开盘买入，更贴近开盘前选股场景

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
python3 -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --entry-price next-open --output table

# Windows
python -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --output table
python -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --output json-cn
python -m app.main backtest-adaptive --start 2025-09-01 --end 2026-03-23 --entry-price next-open --output table
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
- `config/default.stable-v2.yaml`：当前稳健快照
- `config/default.defensive.yaml`：近期防守实验配置，不替代主配置
- `config/default.baseline.yaml`：历史基线
- `config/default.aggressive.yaml`：更激进版本
- `config/default.oversold-neutral.yaml`：偏超跌 / 中性环境变体
- `config/default.oversold-fallback.yaml`：超跌兜底变体

切换配置文件时：

```bash
# macOS / Linux
python3 -m app.main --config config/default.stable-v2.yaml recommend-adaptive --date YYYY-MM-DD

# Windows
python -m app.main --config config/default.stable-v2.yaml recommend-adaptive --date YYYY-MM-DD
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
