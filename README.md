# A-share Daily Picker

Command-line tool to recommend top A-share stocks before open using T-1 close data.

中文说明：一个基于 T-1 收盘数据、用于开盘前选股的 A 股命令行研究工具。

## Quick Start

- 默认配置文件：`config/default.yaml`
- 通用入口：`python3 -m app.main <command> [options]`
- 如需切换配置文件：`python3 -m app.main --config config/default.yaml <command> ...`

## Commands

- `python3 -m app.main recommend --date YYYY-MM-DD [--count N] [--output table|json]`
- `python3 -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal [--output table|json]`
- `python3 -m app.main backtest --start YYYY-MM-DD --end YYYY-MM-DD [--count N] [--output table|json|json-cn]`
- `python3 -m app.main doctor [--output table|json]`
- `python3 -m app.main check-kline --symbol 000001 --start YYYY-MM-DD --end YYYY-MM-DD [--output table|json]`
- `bash scripts/check_today_update.sh`
- `bash scripts/check_today_update_json.sh`
- `bash scripts/check_today_update_multi.sh`

## 中文命令详解

### 全局参数

- `--config`：指定 YAML 配置文件路径；默认是 `config/default.yaml`
- 适用范围：所有子命令
- 常见用途：
  - 保留一份默认配置做日常使用
  - 复制一份保守版/激进版配置做对比测试
  - 回测时临时切换到另一套参数

示例：

```bash
python3 -m app.main --config config/default.yaml recommend --date 2026-03-06
```

### 1) `recommend`

用途：

- 按指定交易日选出推荐股票
- 适合开盘前、盘前研究或每日复盘后生成次日观察名单
- 会根据配置里的过滤器、风险规则、市场环境和评分权重筛选股票

命令：

```bash
python3 -m app.main recommend --date YYYY-MM-DD [--count N] [--output table|json]
```

参数说明：

- `--date YYYY-MM-DD`
  - 目标日期
  - 不传时，程序会按当前日期处理
  - 注意：程序内部会结合交易日逻辑解析“信号日”，并不是简单按自然日生搬硬套
- `--count N`
  - 本次输出推荐数量
  - 不传时，默认使用 `config/default.yaml` 里的 `strategy.pick_count`
- `--output table|json`
  - `table`：适合终端阅读
  - `json`：适合脚本集成、自动化处理或接口对接

输出特点：

- 终端会输出推荐股票、总分、关键指标、推荐理由
- 若 `reporting.enabled: true`，还会额外写入：
  - `reports/recommendations.csv`
  - `reports/recommendations.md`
  - `reports/recommendations.txt`
  - `reports/{signal_date}.log`

常见场景：

- 每天开盘前跑一次，生成候选名单
- 调整 `strategy.enabled_modes` 后观察候选数量变化
- 配合 `explain` 深挖某只股票为何入选

示例：

```bash
python3 -m app.main recommend --date 2026-03-06
python3 -m app.main recommend --date 2026-03-06 --count 5
python3 -m app.main recommend --date 2026-03-06 --output json
```

### 2) `explain`

用途：

- 解释某只股票在指定日期的评分结果
- 适合回答“为什么这只股票被选中/没被选中”
- 常用于调参数、排查过滤条件过严、查看分项得分结构

命令：

```bash
python3 -m app.main explain --symbol 000001 --date YYYY-MM-DD --mode normal [--output table|json]
```

参数说明：

- `--symbol 000001`
  - 必填，股票代码
- `--date YYYY-MM-DD`
  - 目标日期，不传时默认今天
- `--mode normal|relaxed|force`
  - `normal`：正常/严格模式
  - `relaxed`：放宽筛选条件
  - `force`：尽量给结果的兜底模式
- `--output table|json`
  - `table`：人读更直观
  - `json`：便于对接脚本或保存结构化结果

输出内容：

- 总分 `score_total`
- 各分项得分 `score_breakdown`
- 关键指标 `key_metrics`
- 推荐/解释理由 `reason`

常见场景：

- 想知道推荐股票是靠趋势分高，还是靠动量分高
- 想排查某只股票在 `normal` 模式下为何被过滤
- 对比 `normal` 与 `relaxed` 模式下的差异

示例：

```bash
python3 -m app.main explain --symbol 000001 --date 2026-03-06 --mode normal
python3 -m app.main explain --symbol 600519 --date 2026-03-06 --mode relaxed --output json
```

### 3) `backtest`

用途：

- 按历史区间回测策略表现
- 检查策略在过去一段时间内的胜率、平均收益、最大回撤代理等指标
- 适合用来比较不同配置参数的效果

命令：

```bash
python3 -m app.main backtest --start YYYY-MM-DD --end YYYY-MM-DD [--count N] [--output table|json|json-cn]
```

参数说明：

- `--start YYYY-MM-DD`
  - 必填，回测起始日期
- `--end YYYY-MM-DD`
  - 必填，回测结束日期
- `--count N`
  - 每个交易日选几只股票
  - 不传时，默认使用 `strategy.pick_count`
- `--output table|json|json-cn`
  - `table`：终端表格式阅读
  - `json`：英文 key 的 JSON
  - `json-cn`：中文 key 的 JSON，适合直接给中文环境脚本/报表消费

输出指标示例：

- 回测区间
- 尝试交易日 / 跳过交易日
- 交易次数
- 1 日 / 3 日胜率（毛、净）
- 1 日 / 3 日 / 5 日平均收益（毛、净）
- 最大回撤代理
- 模式分布、错误统计、错误示例

说明：

- 回测净收益会考虑 `execution_cost` 里的佣金、印花税、滑点等参数
- 如果区间太短，可能出现“交易日不足”类报错
- 如果某些日期没有足够候选，会记录跳过原因或降级模式结果

示例：

```bash
python3 -m app.main backtest --start 2026-02-01 --end 2026-03-01
python3 -m app.main backtest --start 2026-02-01 --end 2026-03-01 --count 5
python3 -m app.main backtest --start 2026-02-01 --end 2026-03-01 --output json-cn
```

### 4) `doctor`

用途：

- 诊断数据源连通性与基础网络问题
- 适合在“突然跑不动”“拉不到数据”“怀疑被代理污染”时先做自检

命令：

```bash
python3 -m app.main doctor [--output table|json]
```

参数说明：

- `--output table|json`
  - `table`：终端查看
  - `json`：便于自动采集结果

会检查的方向通常包括：

- DNS 是否可解析
- HTTP 请求是否可访问
- 数据源是否能正常连通
- 当前网络环境是否存在明显异常

示例：

```bash
python3 -m app.main doctor
python3 -m app.main doctor --output json
```

### 5) `check-kline`

用途：

- 检查某只股票在指定区间内的日线数据是否能正常抓取
- 适合单点排查：到底是全局网络问题，还是个股/日期区间问题

命令：

```bash
python3 -m app.main check-kline --symbol 000001 --start YYYY-MM-DD --end YYYY-MM-DD [--output table|json]
```

参数说明：

- `--symbol`
  - 必填，股票代码
- `--start`
  - 必填，起始日期
- `--end`
  - 必填，结束日期
- `--output table|json`
  - `table`：显示抓到多少条数据、首尾日期
  - `json`：结构化输出，便于调试脚本

输出内容：

- 股票代码
- 查询区间
- 返回行数
- 首条日期
- 末条日期

示例：

```bash
python3 -m app.main check-kline --symbol 000001 --start 2026-02-01 --end 2026-03-01
python3 -m app.main check-kline --symbol 600519 --start 2026-02-01 --end 2026-03-01 --output json
```

## Freshness Scripts

### `scripts/check_today_update.sh`

用途：

- 用表格方式检查“今天交易日”的数据是否已更新
- 适合手动在终端快速确认

### `scripts/check_today_update_json.sh`

用途：

- 与上面相同，但输出 JSON
- 适合脚本、定时任务、CI 或自动监控接入

### `scripts/check_today_update_multi.sh`

用途：

- 用多只探针股票联合检查数据是否更新
- 适合对单一探针不放心时使用

### `scripts/check_data_freshness.py`

用途：

- 通用数据新鲜度检查脚本
- 可手动指定日期、探针股票、通过规则和输出格式

常用参数：

- `--date YYYY-MM-DD`：检查指定日期，默认今天
- `--probe-symbol 000001`：指定探针股票，可重复传参
- `--require any|all`：多个探针采用任一通过或全部通过规则
- `--output table|json`：输出格式

退出码：

- `0`：数据已更新
- `2`：数据未更新
- `1`：脚本执行异常

## 配置与运行建议

- 默认配置文件：`config/default.yaml`
- 本地缓存默认开启，缓存目录为 `.cache/akshare`
- 每次 `recommend` 会把结构化结果写入 `reports/` 目录
- `reports/*.log` 属于运行日志，通常不建议提交到代码仓库
- 如果你网络环境里代理比较乱，建议保留：
  - `network.disable_env_proxy: true`
  - `network.force_no_proxy_all: true`

## 中文注意事项

- 本项目用于策略研究，不构成投资建议。
- `recommend` 输出的是“候选/研究结果”，不是保证收益的交易信号。
- 若 `signal_date` 对应数据尚未更新，程序可能会告警或直接停止，取决于 `data_freshness.stop_on_stale` 与 `market_filter.stop_on_stale` 设置。
- 默认启用本地缓存，重复运行会更快；若怀疑缓存有问题，可临时关闭 `data_source.cache_enabled` 排查。
- 若 `normal` 模式候选为 0，可考虑查看 `strategy.enabled_modes`、`fallback.mode`、`risk_filter` 和 `market_filter`。

## 中文常见问题

- 回测报错“交易日不足”：`backtest` 至少需要足够交易日样本，需扩大 `--start/--end` 区间。
- 回测里 `normal candidates=0`：常见于熊市拦截、阈值过严、板块过滤过多或历史数据不完整。
- 终端出现 `NotOpenSSLWarning`：这是 Python/urllib3 环境告警，不是本项目核心逻辑错误。
- 运行后生成很多日志：这是 `reporting.recommendation_log` 在工作，默认写到 `reports/{signal_date}.log`。
