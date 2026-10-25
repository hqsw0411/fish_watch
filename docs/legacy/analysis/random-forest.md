> 历史来源文档：保留原始说明与数值，未在本次清理中复测。路径与操作步骤请以仓库根 README 和 docs/deployment.md 为准。

# 随机森林捕捞努力量预测 — 实验记录

## 1. 这一版做了什么

- 数据源：支持两种
  - **SQLite（默认）**：从 `fisheries_forecast.sqlite3` 读，三表 JOIN（effort_cell_period + grid_cell + time_dimension + environment_observation 透视环境因子）。
  - **CSV**：直读 `fleet-monthly-csvs-10-v3-2012/` 下 12 个月网格月度 CSV（2012 全年）。
- 任务：用当月已知的环境/位置信息，回顾性估算每个 0.1° 网格的 `fishing_hours`（nowcast），不是未来预报。
- 模型：`RandomForestRegressor`，目标做 `log1p`，评估时 `expm1` 还原回小时尺度。
- 验证方式：三种 split_mode（见下文第 6 节）。

## 2. 特征

实际进入模型的列（`build_feature_list` 自动根据列是否存在决定）：

- `cell_ll_lat`, `cell_ll_lon`（空间）
- `month_sin`, `month_cos`（把月份编码成圆周坐标，避免把 12 月和 1 月当成最远距离）
- `sst_c`, `chl_mg_m3`, `npp_mg_c_m3_day`, `ssh_m`, `ssha_m`（环境因子，已接入数据库）

明确禁用：`hours`（渔船活动总时长，和目标高度同源）、`geartype`、`flag`、`mmsi_present`、`source_row_count`。

## 3. 样本状态

按约定：

- `fishing_hours > 0` → `observed`
- `fishing_hours == 0` 且 `hours > 0` → `reported_zero`
- 该网格该月完全没出现 → `no_record`

SQLite 和 CSV 里都只有"出现过的网格"，所以 `no_record` 这一版没法直接构造。`observed` 和 `reported_zero` 都进训练集——零捕捞是有效信息，不能当缺失值扔掉。

## 4. 基线

两个，都比随机森林差才算数：

- 全局均值：用训练集 `fishing_hours` 的整体均值预测所有测试点。
- 月份均值：用训练集最后一个时间片的均值预测测试集（不泄漏测试期信息）。

## 5. 指标口径

R²、RMSE、MAE 都是在**原始小时尺度**（expm1 还原后）上算的，不是 log 空间。
`slope/intercept` 是把 `y_pred` 对 `y_true` 做一元线性回归得到的——理想模型应该接近 `slope=1, intercept=0`。

## 6. 切分模式（--split_mode）

三种：

- **auto（默认）**：按时间排序，取最后 N 个（年,月）时间片做测试集，前面全部训练。`--test_periods N` 控制留几个时间片。数据增长后自动覆盖全部历史。
- **manual**：手动指定月份。`--train_months 1 2 3 4 --test_months 5 6`。
- **none**：全量数据训练，不切测试集，跳过测试指标和 permutation importance。用于最终部署出模型。

## 7. 跑法

```bash
# 默认：从 SQLite 读，auto 切分
python scripts/train_random_forest.py --db_path data/database/fisheries_forecast.sqlite3

# 全量训练出部署模型（不评估）
python scripts/train_random_forest.py --db_path data/database/fisheries_forecast.sqlite3 --split_mode none

# manual 月份留出
python scripts/train_random_forest.py --source csv --csv_dir fleet-monthly-csvs-10-v3-2012 \
  --split_mode manual --train_months 1 2 3 4 --test_months 5 6 --n_estimators 300

# 复算/评估已有 run
python scripts/evaluate_random_forest.py --run_id rf_month_XXXXXXXX_XXXXXX
```

每次训练会在 `models/runs/<run_id>/` 下产出：

- `metrics.json`：配置、样本数、相关矩阵、两个基线、RF 指标、局限性。
- `feature_importance.json`：测试集上的 permutation importance（split_mode=none 时为空）。
- `model.joblib`：训练好的模型本体。

## 8. 已知局限（必须写进交付）

1. 环境因子已接入，但回顾性 nowcast 解释力弱，R² 接近 0，MAE 是主要评估指标。
2. 高共线性：`cell_ll_lon ↔ ssh_m`（r≈-0.93）、`month_cos ↔ sst_c`（r≈0.88），特征重要性会被分摊，解读需谨慎。
3. `no_record` 没处理，样本有截断：只在有渔船出现的网格上训练，完整网格月历未补全。
4. 数据时间跨度有限，季节外推能力未充分验证。
5. 空间块留出法未做，仅按时间/月份切分。
6. 目标分布极度右偏（月均值 4~22，最大值 215~1197），R² 受极端值主导，以 MAE 为准。

## 9. 下一步

- [ ] 做空间块留出验证（2×3 网格块）。
- [ ] 用完整网格月历补 `no_record` 类别。
- [ ] 调 `max_features` / `min_samples_leaf` / `max_depth`，观察是否过拟合。
- [ ] 接入更长时间跨度数据，验证跨年季节稳定性。
