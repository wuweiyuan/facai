# 逐股收益预测排名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为本地 AKShare 缓存中每一只合格股票独立训练 Ridge 回归模型，输出以未来 5 个交易日预期收益排序的 5/10 日预测排名。

**Architecture:** 新增独立的 `app.forecasting` 包，不接入组合选股的 `Recommender` 或 `BacktestRunner`。引擎从本地 CSV 顺序读取单只股票，生成仅使用信号日前数据的特征和标签；为每个预测期限及每只股票用滚动验证选择训练窗口与正则强度，然后拟合最终 Ridge 模型。CLI 只负责解析参数、调用引擎、打印和保存结果。

**Tech Stack:** Python 3.10+、numpy、pandas、PyYAML、unittest；不添加第三方机器学习依赖。

---

## 文件结构

- `app/forecasting/__init__.py`：声明预测包。
- `app/forecasting/models.py`：预测结果、批次摘要的数据类。
- `app/forecasting/features.py`：日线特征、5/10 日标签、Ridge 拟合/预测和逐时点验证；不读文件。
- `app/forecasting/engine.py`：缓存读取、资格校验、逐股训练、排名和跳过统计。
- `app/forecasting/reporting.py`：CSV、JSON 和中文终端表格序列化。
- `app/main.py`：注册并调度 `forecast-rank`。
- `config/default.yaml`：预测默认参数和报表位置。
- `tests/test_forecasting.py`：临时缓存上的预测包和 CLI 测试。
- `README.md`：命令和预测口径说明。

### Task 1: 定义模型结果、特征和 Ridge 原语

**Files:**
- Create: `app/forecasting/__init__.py`
- Create: `app/forecasting/models.py`
- Create: `app/forecasting/features.py`
- Test: `tests/test_forecasting.py`

- [ ] **Step 1: 写入会失败的标签与 Ridge 测试**

```python
def test_training_frame_has_forward_label_but_not_future_feature(self):
    frame = build_training_frame(_bars(90), horizon=5)
    row = frame.iloc[0]
    self.assertAlmostEqual(row["target_return"], row["close_t_plus_h"] / row["close"] - 1.0)
    self.assertNotIn("close_t_plus_h", FEATURE_COLUMNS)

def test_ridge_fit_returns_finite_coefficients(self):
    model = fit_ridge(np.array([[0.0], [1.0], [2.0], [3.0]]), np.array([0.0, 1.0, 2.0, 3.0]), 1.0)
    self.assertTrue(np.isfinite(model.coef_).all())
    self.assertGreater(predict_ridge(model, np.array([[4.0]]))[0], 2.0)
```

- [ ] **Step 2: 验证测试当前失败**

Run: `python3 -m unittest tests.test_forecasting.TestForecastFeatures -v`

Expected: FAIL，`app.forecasting` 和函数尚不存在。

- [ ] **Step 3: 实现最小局部建模接口**

在 `models.py` 定义如下不可变类型，后续任务沿用其字段名：

```python
@dataclass(frozen=True)
class RidgeModel:
    mean_: np.ndarray
    scale_: np.ndarray
    coef_: np.ndarray
    intercept_: float

@dataclass(frozen=True)
class HorizonForecast:
    horizon: int
    expected_return: float
    probability_up: float
    train_window: int
    alpha: float
    validation_mae: float
    validation_direction_accuracy: float
    validation_residuals: tuple[float, ...]

@dataclass(frozen=True)
class StockForecast:
    symbol: str
    name: str
    signal_date: date
    last_bar_date: date
    sample_count: int
    forecast_5d: HorizonForecast
    forecast_10d: HorizonForecast

@dataclass(frozen=True)
class ForecastBatch:
    signal_date: date
    source_last_bar_date: date
    items: tuple[StockForecast, ...]
    skipped: dict[str, int]
```

在 `features.py` 定义：

```python
FEATURE_COLUMNS = (
    "ret_1d", "mom5", "mom20", "close_vs_ma20", "close_vs_ma60",
    "ma20_slope5", "rsi14", "vol20_std", "vol_ratio_5_20",
    "volume_zscore20", "atr14_pct", "turnover_rate",
)

def build_training_frame(bars: pd.DataFrame, horizon: int) -> pd.DataFrame:
    indexed = add_indicators(bars.sort_values("trade_date").reset_index(drop=True))
    indexed["close_vs_ma20"] = indexed["close"] / indexed["ma20"] - 1.0
    indexed["close_vs_ma60"] = indexed["close"] / indexed["ma60"] - 1.0
    indexed["atr14_pct"] = indexed["atr14"] / indexed["close"]
    indexed["close_t_plus_h"] = indexed["close"].shift(-horizon)
    indexed["target_return"] = indexed["close_t_plus_h"] / indexed["close"] - 1.0
    return indexed.dropna(subset=[*FEATURE_COLUMNS, "target_return"]).reset_index(drop=True)
```

实现 `fit_ridge(x, y, alpha)` 和 `predict_ridge(model, x)`。前者仅用训练集保存均值/标准差，零标准差替换为 `1.0`，用 `(X.T @ X + alpha * I)` 求解系数，并恢复截距；后者只读取 `RidgeModel` 参数。目标列和 `close_t_plus_h` 不得出现在 `FEATURE_COLUMNS`。

- [ ] **Step 4: 验证局部模型**

Run: `python3 -m unittest tests.test_forecasting.TestForecastFeatures -v`

Expected: PASS，两个测试通过。

- [ ] **Step 5: 提交局部建模原语**

```bash
git add app/forecasting/__init__.py app/forecasting/models.py app/forecasting/features.py tests/test_forecasting.py
git commit -m "feat: add per-stock ridge forecasting primitives"
```

### Task 2: 用时间顺序为每只股票选择参数

**Files:**
- Modify: `app/forecasting/features.py`
- Test: `tests/test_forecasting.py`

- [ ] **Step 1: 写入会失败的滚动验证测试**

```python
def test_select_horizon_model_uses_earlier_rows_for_each_validation_prediction(self):
    selected = select_horizon_model(
        _training_frame_with_regime_change(), 5, (20, 30), (0.1, 10.0), validation_samples=12
    )
    self.assertIn(selected.train_window, {20, 30})
    self.assertIn(selected.alpha, {0.1, 10.0})
    self.assertEqual(len(selected.validation_residuals), 12)

def test_select_horizon_model_rejects_insufficient_history(self):
    with self.assertRaisesRegex(ValueError, "insufficient training samples"):
        select_horizon_model(_training_frame(30), 5, (40,), (1.0,), validation_samples=10)
```

- [ ] **Step 2: 验证滚动验证测试当前失败**

Run: `python3 -m unittest tests.test_forecasting.TestForecastSelection -v`

Expected: FAIL，`select_horizon_model` 尚未定义。

- [ ] **Step 3: 实现选择和最终训练**

新增：

```python
def select_horizon_model(
    frame: pd.DataFrame, horizon: int, candidate_windows: tuple[int, ...],
    candidate_alphas: tuple[float, ...], validation_samples: int,
) -> HorizonForecast: ...

def train_final_model(frame: pd.DataFrame, train_window: int, alpha: float) -> tuple[RidgeModel, pd.Series]: ...
```

对每个 `(window, alpha)`，末尾 `validation_samples` 行是验证时点。验证时点 `i` 只能用 `frame.iloc[max(0, i-window):i]` 拟合；少于 `window` 行则候选无效。积累 `actual - prediction`，以 MAE 选择候选；相同 MAE 时选择较小窗口、再选择较小 alpha。最终训练只使用选择窗口内、标签已落地的行。上涨概率函数固定为：

```python
def probability_up(prediction: float, residuals: tuple[float, ...]) -> float:
    return sum(prediction + residual > 0 for residual in residuals) / len(residuals)
```

方向准确率比较验证预测和实际收益是否同号。

- [ ] **Step 4: 验证滚动验证行为**

Run: `python3 -m unittest tests.test_forecasting.TestForecastSelection -v`

Expected: PASS，残差数量固定，历史不足时明确失败。

- [ ] **Step 5: 提交参数选择**

```bash
git add app/forecasting/features.py tests/test_forecasting.py
git commit -m "feat: select ridge parameters per stock chronologically"
```

### Task 3: 从缓存批量生成逐股预测和稳定排名

**Files:**
- Create: `app/forecasting/engine.py`
- Test: `tests/test_forecasting.py`

- [ ] **Step 1: 写入会失败的缓存引擎测试**

```python
def test_engine_trains_each_eligible_stock_and_ranks_by_expected_5d_return(self):
    root = self._write_cache({"000001": _bars(330, drift=0.004), "000002": _bars(330, drift=-0.002)})
    batch = ForecastEngine(_config(root)).rank(date(2026, 4, 6))
    self.assertEqual([item.symbol for item in batch.items], ["000001", "000002"])
    self.assertGreater(batch.items[0].forecast_5d.expected_return, batch.items[1].forecast_5d.expected_return)

def test_engine_skips_stale_and_short_history_without_stopping_batch(self):
    root = self._write_cache({"000001": _bars(330), "000003": _bars(100), "000004": _bars(300, end_days_early=10)})
    batch = ForecastEngine(_config(root)).rank(date(2026, 4, 6))
    self.assertEqual([item.symbol for item in batch.items], ["000001"])
    self.assertEqual(batch.skipped["insufficient_history"], 1)
    self.assertEqual(batch.skipped["stale_bar"], 1)
```

- [ ] **Step 2: 验证引擎测试当前失败**

Run: `python3 -m unittest tests.test_forecasting.TestForecastEngine -v`

Expected: FAIL，`ForecastEngine` 尚不存在。

- [ ] **Step 3: 实现本地缓存引擎**

实现 `ForecastEngine(config: dict)`：

```python
def rank(self, signal_date: date | None = None) -> ForecastBatch: ...
def _forecast_symbol(self, path: Path, name: str, signal_date: date, latest_date: date) -> StockForecast: ...
```

从 `data_source.cache_dir/meta/stock_list.csv` 读名称，从 `bars/*.csv` 顺序读数据；不创建 `AkshareDataSource`、不联网、不写缓存。默认信号日是全部 bar 文件最后日期的最大值。单股先截断到 `trade_date <= signal_date`，末 bar 不是信号日计入 `stale_bar`；少于 `forecasting.min_history_bars` 计入 `insufficient_history`。缺列、重复/乱序日期、CSV 读取和拟合错误要分别稳定计数，单股失败继续扫描。

为 5 和 10 分别构造训练帧、选择参数、最终拟合，并对最后一条仅含过去特征的可预测行计算收益与概率。排序键必须完全为：

```python
key=lambda item: (
    -item.forecast_5d.expected_return,
    -item.forecast_5d.probability_up,
    item.forecast_5d.validation_mae,
    item.symbol,
)
```

- [ ] **Step 4: 验证批量预测、跳过和排序**

Run: `python3 -m unittest tests.test_forecasting.TestForecastEngine -v`

Expected: PASS，合格股票拥有各自的 5/10 日模型；不合格文件只进入摘要。

- [ ] **Step 5: 提交批量引擎**

```bash
git add app/forecasting/engine.py tests/test_forecasting.py
git commit -m "feat: rank independently trained stock forecasts"
```

### Task 4: 添加报表、配置与 CLI

**Files:**
- Create: `app/forecasting/reporting.py`
- Modify: `config/default.yaml`
- Modify: `app/main.py:build_parser`
- Modify: `app/main.py:main`
- Test: `tests/test_forecasting.py`

- [ ] **Step 1: 写入会失败的报表和 CLI 测试**

```python
def test_forecast_csv_and_json_include_model_metadata(self):
    batch = _forecast_batch()
    saved = write_forecast_csv(batch, self.tmp_path / "forecast_rank.csv")
    columns = pd.read_csv(saved).columns.tolist()
    self.assertEqual(columns[:4], ["rank", "symbol", "name", "signal_date"])
    self.assertIn("expected_return_5d", columns)
    self.assertIn("ridge_alpha_10d", columns)
    self.assertEqual(len(batch_to_dict(batch, limit=1)["items"]), 1)

def test_forecast_rank_parser_accepts_output_count_date_and_no_save(self):
    args = build_parser().parse_args(
        ["forecast-rank", "--date", "2026-07-13", "--count", "7", "--output", "json", "--no-save"]
    )
    self.assertEqual((args.cmd, args.count, args.output, args.no_save), ("forecast-rank", 7, "json", True))
```

- [ ] **Step 2: 验证报表和 CLI 测试当前失败**

Run: `python3 -m unittest tests.test_forecasting.TestForecastReporting tests.test_forecasting.TestForecastCli -v`

Expected: FAIL，报表模块和命令尚不存在。

- [ ] **Step 3: 实现序列化、默认参数和命令**

`reporting.py` 提供 `forecast_rows(batch)`、`write_forecast_csv(batch, path)`、`batch_to_dict(batch, limit)`、`format_forecast_table(batch, limit)`。CSV 使用固定字段：

```text
rank,symbol,name,signal_date,last_bar_date,sample_count,
expected_return_5d,probability_up_5d,train_window_5d,ridge_alpha_5d,validation_mae_5d,validation_direction_accuracy_5d,
expected_return_10d,probability_up_10d,train_window_10d,ridge_alpha_10d,validation_mae_10d,validation_direction_accuracy_10d
```

JSON 有 `signal_date`、`source_last_bar_date`、`summary: {eligible_count, skipped}` 和截断后的 `items`。表格首行显示信号日、合格数和跳过摘要，百分比采用 `.2%`。

向 `config/default.yaml` 加入：

```yaml
forecasting:
  count: 100
  min_history_bars: 252
  min_train_samples: 120
  validation_samples: 60
  candidate_train_windows: [120, 180, 240]
  ridge_alphas: [0.1, 1.0, 10.0]
  report_csv: reports/forecast_rank.csv
```

在 `build_parser()` 增加：

```python
p_forecast = sub.add_parser("forecast-rank", help="Independently forecast and rank cached stocks by expected 5-day return")
p_forecast.add_argument("--date", default=None, help="Signal date YYYY-MM-DD; defaults to latest cached bar date")
p_forecast.add_argument("--count", type=int, default=None, help="How many ranked stocks to display")
p_forecast.add_argument("--output", choices=["table", "json"], default="table")
p_forecast.add_argument("--no-save", action="store_true", help="Do not write the full CSV report")
```

在 `main()` 的联网分支之前加载 `ForecastEngine(base_cfg)`，调用 `rank(_parse_date(args.date) if args.date else None)`，以 `args.count or forecasting.count` 展示；除非 `--no-save`，写 `forecasting.report_csv`。JSON 仅使用 `json.dumps(batch_to_dict(...), ensure_ascii=False, indent=2)`，table 仅使用 `format_forecast_table`。此命令不得调用 `_configure_network` 或 `_build_data_source`。

- [ ] **Step 4: 验证报表和命令接口**

Run: `python3 -m unittest tests.test_forecasting.TestForecastReporting tests.test_forecasting.TestForecastCli -v`

Expected: PASS，CSV/JSON 字段稳定、解析器可用，且 `--no-save` 不创建报表。

- [ ] **Step 5: 提交报表、配置和 CLI**

```bash
git add app/forecasting/reporting.py config/default.yaml app/main.py tests/test_forecasting.py
git commit -m "feat: add forecast rank command and reports"
```

### Task 5: 补充文档并完成回归验证

**Files:**
- Modify: `tests/test_forecasting.py`
- Modify: `README.md`

- [ ] **Step 1: 写入确定性排序测试**

```python
def test_rank_is_deterministic_for_equal_predictions(self):
    root = self._write_cache({"000002": _flat_bars(330), "000001": _flat_bars(330)})
    first = ForecastEngine(_config(root)).rank()
    second = ForecastEngine(_config(root)).rank()
    self.assertEqual([item.symbol for item in first.items], [item.symbol for item in second.items])
    self.assertEqual([item.symbol for item in first.items], sorted(item.symbol for item in first.items))
```

- [ ] **Step 2: 更新 README**

在“日常使用建议”写入：

```bash
python3 -m app.main forecast-rank --count 100 --output table
python3 -m app.main forecast-rank --date 2026-07-13 --count 20 --output json --no-save
```

说明主排序是 5 日预期收盘收益；模型逐股使用该股票自身历史数据和滚动验证，10 日预测为辅助信息；预测不包含交易成本或实时行情，且不构成收益承诺。

- [ ] **Step 3: 运行新增模块测试**

Run: `python3 -m unittest tests.test_forecasting -v`

Expected: PASS，目标、无泄漏、逐股参数、资格跳过、排序、报表和 CLI 测试均通过。

- [ ] **Step 4: 运行完整自动化测试**

Run: `python3 -m unittest discover -s tests -v`

Expected: PASS，既有选股、回测和新增预测测试全部通过。

- [ ] **Step 5: 用真实缓存做无写入冒烟测试并提交**

Run: `python3 -m app.main forecast-rank --count 10 --output table --no-save`

Expected: 输出最新缓存信号日、合格/跳过摘要与 10 条按 5 日预期收益降序排列的股票；不访问网络，不改写 `.cache/akshare`。

```bash
git status --short
git add tests/test_forecasting.py README.md
git commit -m "test: verify deterministic stock forecast ranking"
```

只暂存本计划列出的预测功能文件；保留无关的 `reports/intraday_pick_signals.jsonl` 改动。

