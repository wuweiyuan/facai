# 预测排名日志设计

## 目标

让 `forecast-rank` 在保留全量 CSV 的同时，将终端展示的前 N 名结果保存为可阅读的文本日志，使用与 `auction-pick`、`tail-pick` 一致的日期归档和最新结果入口。

## 输出约定

默认运行：

```bash
python3 -m app.main forecast-rank
```

除 `reports/forecast_rank.csv` 外，额外写入：

```text
reports/forecast_rank/YYYY-MM-DD.log
reports/forecast_rank/latest.log
```

- 日期文件名使用本次信号日的 ISO 日期。
- 当日多次运行时，日期日志追加每次完整的终端表格结果，并以空行分隔。
- `latest.log` 每次覆盖为最新一次完整表格结果。
- 表格与终端显示一致：信号日、合格/跳过摘要、排名、5/10 日预期收益、方向准确率、MAE 和 5 日模型参数。
- `--count` 决定终端和两个日志中写入的条数；默认仍由 `forecasting.count` 控制。
- `--no-save` 不写 CSV，也不写日期日志或 `latest.log`。

## 代码边界

- `app/forecasting/reporting.py` 新增一个日志写入函数，负责创建目录、追加日期文件和原子性更新 `latest.log`；不改变预测、排序或 CSV 序列化。
- `app/main.py` 的 `forecast-rank` 分支在已有表格文本生成后调用日志函数；JSON 输出保持 JSON，不写文本日志，避免混合格式。
- `config/default.yaml` 增加 `forecasting.report_log_dir: reports/forecast_rank`，以便配置日志目录。

## 测试

使用临时目录验证：

- 首次写入创建日期日志和 `latest.log`；
- 同一日期第二次写入会追加日期日志、覆盖 `latest.log`；
- 日志内容等于输入表格文本；
- `--no-save` 的 CLI 路径不调用 CSV 或日志写入。

## 验收标准

一次默认 `forecast-rank` 运行后，用户可以通过 `reports/forecast_rank/latest.log` 查看最近前 N 名，通过日期日志回看当天的运行记录，且原有 CSV 和排名结果不变。
