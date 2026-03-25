# 最终版策略总结

## 当前正式方案

- 正式主入口：`recommend-adaptive`
- 正式主回测：`backtest-adaptive`
- 正式主配置：`config/default.yaml`
- 稳定快照：`config/default.stable.yaml`
- 历史基线：`config/default.baseline.yaml`

当前正式方案的核心结构：

- 主策略：`recommend-pullback`
- 弱市补充：`recommend-oversold`
- 无信号：空仓

## 已验证有效的优化

以下优化已经通过本地 `.cache/akshare/bars` 数据验证，当前保留在 `config/default.yaml`：

1. `pullback` 轻度放宽到当前有效区间
2. `pullback` 支持 `normal -> relaxed` 兜底
3. `bear` 市中使用 `recommend-oversold -> recommend-pullback`
4. `adaptive` 中不同策略使用不同默认选股数量

这些优化带来的结果：

- 相比 baseline，交易次数增加
- 空仓减少
- `3日/5日` 净收益提升
- 最大回撤代理下降

## 已验证失败或不建议接入主流程的实验

以下方向已经做过验证，但结果不理想，不建议继续接入当前主流程：

### 1. 继续微调 `pullback` 小参数

包括：

- `min_mom20`
- `max_mom5`
- `max_close_above_ma20_pct`

结论：

- 多轮 A/B 对比显示，这些继续微调几乎没有新增效果

### 2. `recommend-bull`

结论：

- 收益弱于正式主流程
- 回撤明显更高
- 不值得放回 `adaptive`

### 3. `recommend-relative`

结论：

- 无论是否接相对市场强弱，收益都明显弱于主流程
- 回撤明显更高
- 不值得放回 `adaptive`

### 4. 板块增强版 `recommend-relative`

前提：

- 已接入 `data/sector_map.csv`
- 已使用真实 sector map 和板块动量

结论：

- 仍然没有带来比正式主流程更好的结果
- 不值得放回 `adaptive`

### 5. 第一版 `backtest-adaptive-rules`

结论：

- 当前实现下，规则退出回测结果弱于固定持有期回测
- 目前不建议替代 `backtest-adaptive`

## 当前系统的边界

基于现有数据和实验结果，可以确认：

1. 现有系统已经接近当前数据条件下的局部最优
2. 再继续靠现有 OHLCV + 指数状态 + 技术形态逻辑调参数，边际收益很低
3. 想进一步明显减少空仓、增加收益、降低回撤，不能再靠现有小参数微调

## 现阶段推荐的日常使用方式

### 盘前正式信号

```bash
python3 -m app.main recommend-adaptive --date YYYY-MM-DD
```

### 固定持有期回测

```bash
python3 -m app.main backtest-adaptive --start YYYY-MM-DD --end YYYY-MM-DD --output table
```

### 优化前后 A/B 对比

```bash
python3 scripts/compare_adaptive_configs.py --start YYYY-MM-DD --end YYYY-MM-DD --output table
```

### 空仓时的观察池

```bash
python3 -m app.main recommend-opportunity --date YYYY-MM-DD
```

说明：

- `recommend-opportunity` 只用于观察，不等于正式交易信号

## 下一阶段如果继续投入，建议方向

以下方向比继续调现有参数更值得投入：

### 1. 更高维度数据

优先级较高：

- 更细粒度的板块热度/轮动数据
- 开盘后可交易性数据
- 更高频或更贴近实盘执行的数据

### 2. 重做退出规则回测

不是沿用当前第一版规则退出，而是重新设计：

- 分策略的止盈/止损
- 分市场状态的退出规则
- 与真实执行更贴近的卖出逻辑

## 最终结论

当前最值得继续使用的版本就是：

- `config/default.yaml`
- `recommend-adaptive`
- `backtest-adaptive`

在现有数据条件下，不建议继续往主流程里添加新的选股策略分支。
