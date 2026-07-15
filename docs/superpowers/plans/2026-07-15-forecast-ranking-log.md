# 预测排名日志 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `forecast-rank` 将终端展示的前 N 名同步归档为日期日志和最新日志。

**Architecture:** 预测、排序和 CSV 保持原样。在 `app.forecasting.reporting` 集中实现文本日志持久化：日期文件追加、`latest.log` 覆盖；CLI 仅在非 JSON、非 `--no-save` 的情况下调用它。

**Tech Stack:** Python 3.10+、pathlib、unittest。

---

## 文件结构

- `app/forecasting/reporting.py`：新增 `write_forecast_log(rendered, signal_date, log_dir)`。
- `app/main.py`：在 `forecast-rank` 的 table 输出路径写日志。
- `config/default.yaml`：增加日志目录配置。
- `tests/test_forecasting.py`：日志写入与 CLI 输出回归测试。
- `README.md`：说明日志位置和查看命令。

### Task 1: 以测试定义文本日志写入

**Files:**
- Modify: `tests/test_forecasting.py`
- Modify: `app/forecasting/reporting.py`

- [ ] **Step 1: 写入失败测试**

```python
def test_write_forecast_log_appends_daily_log_and_replaces_latest(self):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = write_forecast_log("first\n", date(2026, 7, 15), root)
        second = write_forecast_log("second\n", date(2026, 7, 15), root)
        self.assertEqual(first, root / "2026-07-15.log")
        self.assertEqual(second.read_text(encoding="utf-8"), "first\n\nsecond\n")
        self.assertEqual((root / "latest.log").read_text(encoding="utf-8"), "second\n")
```

- [ ] **Step 2: 验证测试失败**

Run: `python3 -m unittest tests.test_forecasting.TestForecastReporting.test_write_forecast_log_appends_daily_log_and_replaces_latest -v`

Expected: FAIL，`write_forecast_log` 尚不存在。

- [ ] **Step 3: 实现最小日志函数**

在 `app/forecasting/reporting.py` 增加：

```python
def write_forecast_log(rendered: str, signal_date: date, log_dir: str | Path) -> Path:
    root = Path(log_dir)
    root.mkdir(parents=True, exist_ok=True)
    content = rendered if rendered.endswith("\n") else rendered + "\n"
    daily = root / f"{signal_date.isoformat()}.log"
    with daily.open("a", encoding="utf-8") as handle:
        if daily.stat().st_size > 0:
            handle.write("\n")
        handle.write(content)
    (root / "latest.log").write_text(content, encoding="utf-8")
    return daily
```

- [ ] **Step 4: 验证日志测试通过**

Run: `python3 -m unittest tests.test_forecasting.TestForecastReporting -v`

Expected: PASS。

- [ ] **Step 5: 提交日志函数**

```bash
git add app/forecasting/reporting.py tests/test_forecasting.py
git commit -m "feat: add forecast ranking text logs"
```

### Task 2: 接入 CLI、配置和使用说明

**Files:**
- Modify: `app/main.py`
- Modify: `config/default.yaml`
- Modify: `README.md`
- Modify: `tests/test_forecasting.py`

- [ ] **Step 1: 写入失败的 CLI 路径测试**

```python
@patch("app.forecasting.reporting.write_forecast_log")
def test_forecast_rank_table_saves_rank_log(self, log_mock):
    rank_mock.return_value = _forecast_batch()
    with patch.object(sys, "argv", ["app.main", "forecast-rank", "--no-save"]):
        main()
    log_mock.assert_not_called()
```

并补充未传 `--no-save` 时断言 `write_forecast_log` 获得表格文本、信号日和配置中的日志目录。

- [ ] **Step 2: 验证 CLI 测试失败**

Run: `python3 -m unittest tests.test_forecasting.TestForecastCli -v`

Expected: FAIL，因为 CLI 还未调用日志函数。

- [ ] **Step 3: 接入 table 日志**

在 `config/default.yaml` 的 `forecasting` 节加入：

```yaml
report_log_dir: reports/forecast_rank
```

在 `app/main.py` 的 `forecast-rank` 分支先生成：

```python
rendered = format_forecast_table(batch, limit)
```

仅在 `not args.no_save and args.output == "table"` 时，调用：

```python
write_forecast_log(rendered, batch.signal_date, forecast_cfg.get("report_log_dir", "reports/forecast_rank"))
```

仍按现有逻辑保存全量 CSV；JSON 输出不写文本日志；`--no-save` 不写 CSV 或日志。README 增加：

```text
reports/forecast_rank/YYYY-MM-DD.log
reports/forecast_rank/latest.log
```

以及 `tail -n 80 reports/forecast_rank/latest.log` 查看示例。

- [ ] **Step 4: 验证 CLI 和完整预测测试**

Run: `python3 -m unittest tests.test_forecasting -v`

Expected: PASS，默认 table 会写日志，JSON 与 `--no-save` 不写。

- [ ] **Step 5: 提交 CLI、配置和说明**

```bash
git add app/main.py config/default.yaml README.md tests/test_forecasting.py
git commit -m "feat: save forecast ranking logs"
```

### Task 3: 回归验证

**Files:**
- Test: `tests/test_forecasting.py`

- [ ] **Step 1: 运行全套测试**

Run: `python3 -m unittest discover -s tests -v`

Expected: PASS。

- [ ] **Step 2: 以本地缓存运行一次前十名**

Run: `python3 -m app.main forecast-rank --count 10`

Expected: 终端表格、`reports/forecast_rank.csv`、`reports/forecast_rank/YYYY-MM-DD.log` 和 `reports/forecast_rank/latest.log` 都存在；两个日志的前十名文本等同终端输出。

- [ ] **Step 3: 提交最终验证变更**

```bash
git status --short
git add tests/test_forecasting.py README.md
git commit -m "test: verify forecast ranking log output"
```

仅暂存本计划列出的预测日志文件，不混入既有报表内容。

