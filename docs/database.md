> 数据库设计与原始构建记录。历史样本计数和日期未在本次结构整理中复测；当前操作入口见 README 和 tools/data/build_database.py。

# 数据库实施说明

> 构建日期：2026-10-16<br>
> 数据库：SQLite 3<br>
> 文件：data/database/fisheries_forecast.sqlite3<br>
> 构建脚本：-m tools.data.build_database

## 1. 已完成内容

已按照《数据库环境要素整合方案》建立独立数据库。数据库与现有前端浏览缓存 data/samples/patagonia_6blocks_2012_h1/sample.sqlite3 分开，主项目后续通过数据服务访问本数据库，不依赖内部表名。

本次实际导入：

| 内容 | 数量 |
|---|---:|
| 时间月份 | 6（2012-01 至 2012-06） |
| 目标网格 | 0.1° WGS84 / EPSG:4326 |
| 每月网格 | 2,400 |
| 网格月聚合记录 | 14,400 |
| GFW 原始 flag-geartype 努力量记录 | 7,513 |
| 环境观测记录 | 72,000（14,400 × 5） |
| 模型特征记录 | 144,000（14,400 × 10） |
| 网格单元 | 2,400 |
| 环境数据源 | 3 个 CMEMS 产品 |
| 努力量数据源 | GFW/Zenodo v3.0 |
| 模型运行记录 | 0（数据库已预留） |
| 预测结果记录 | 0（数据库已预留） |

区域为 62°W—56°W、48°S—44°S。GFW 原始压缩包为本地已校验的 fleet-monthly-csvs-10-v3-2012.zip；环境值来自项目已经下载并记录版本的 CMEMS NetCDF。

## 2. 数据实际存储内容

### 2.1 数据源和追溯

data_source 保存数据集级来源：

- GFW Global AIS-based Apparent Fishing Effort Dataset，v3.0，DOI 10.5281/zenodo.14982712，CC BY-NC 4.0；
- CMEMS Physics cmems_mod_glo_phy_my_0.083deg_P1M-m，版本 202311；
- CMEMS BGC cmems_mod_glo_bgc_my_0.25deg_P1M-m，版本 202406；
- CMEMS DUACS Altimetry cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1M-m，版本 202411。

source_asset 保存具体文件/对象，包括路径、格式、空间范围、时间范围、文件大小、SHA-256、MD5、下载方式和元数据 JSON。

### 2.2 努力量

原始表 effort_observation 保留 GFW 的 flag/geartype 粒度，当前数据包括 17 个船旗代码和 7 种实际出现的渔具类型。字段为：

| 字段 | 类型 | 含义 |
|---|---|---|
| dataset_version_id | TEXT | gfw_v3.0 |
| time_id | TEXT | YYYY-MM |
| cell_id | TEXT | 0.1° 网格稳定 ID |
| flag_code | TEXT | GFW 船旗代码 |
| gear_type | TEXT | GFW 渔具类型 |
| hours | REAL | AIS 船舶存在小时 |
| fishing_hours | REAL | GFW 模型推断的表观捕捞小时 |
| mmsi_present | INTEGER | 该 flag/geartype 组合中的 MMSI 数 |
| source_asset_id | TEXT | 原始 GFW 压缩包资产 ID |
| source_row_count | INTEGER | 对应原始记录数，当前为 1 |
| effort_status | TEXT | observed 或 reported_zero |
| quality_flags | TEXT | 质量备注 |
| created_at | TEXT | 入库时间 |

聚合表 effort_cell_period 是模型和 API 使用的目标层，每行对应一个“月份 × 网格 × 目标口径”，当前目标口径为 all_gear：

| 字段 | 类型 | 含义 |
|---|---|---|
| dataset_version_id | TEXT | GFW 数据版本 |
| time_id | TEXT | 月份 |
| cell_id | TEXT | 网格 ID |
| target_scope | TEXT | all_gear |
| hours | REAL | 网格月内各 flag/geartype 的 hours 求和 |
| fishing_hours | REAL | 网格月内各 flag/geartype 的 fishing_hours 求和 |
| source_row_count | INTEGER | 该网格月原始行数 |
| gear_types | TEXT | 去重后的渔具类型，分号连接 |
| effort_status | TEXT | observed、reported_zero、no_record |
| energy_kwh | REAL | 当前为 NULL |
| energy_status | TEXT | 当前为 not_available |
| aggregation_rule | TEXT | 聚合口径说明 |

no_record 没有被填成 0。当前每月 2,400 个网格中的无源记录数分别为：

| 月份 | 无源记录网格 | fishing_hours |
|---|---:|---:|
| 2012-01 | 1,775 | 8,572.1918 |
| 2012-02 | 1,370 | 17,721.2389 |
| 2012-03 | 1,539 | 18,672.2611 |
| 2012-04 | 1,609 | 6,495.2017 |
| 2012-05 | 1,593 | 3,664.9962 |
| 2012-06 | 1,821 | 4,021.4224 |

原始表与聚合表的 fishing_hours 总和均为 59,147.3121，hours 总和均为 151,649.975，已通过守恒检查。

### 2.3 环境观测

environment_observation 使用长表，一行对应一个“源资产 × 变量 × 网格 × 月份”：

| 字段 | 类型 | 含义 |
|---|---|---|
| source_asset_id | TEXT | CMEMS 资产 |
| variable_id | TEXT | 统一变量定义 ID |
| grid_id | TEXT | gfw_0p1deg_epsg4326 |
| cell_id | TEXT | 目标网格 |
| time_id | TEXT | 月份 |
| value | REAL | 对齐后的数值 |
| depth_m | REAL | 取值深度；表层代理约 0.494/0.506 m |
| valid_flag | INTEGER | 1 有效，0 无效 |
| missing_reason | TEXT | 缺测原因 |
| spatial_method | TEXT | 当前为最近源网格中心 |
| temporal_method | TEXT | 同自然月匹配 |
| coverage_fraction | REAL | 有效覆盖比例 |
| created_at | TEXT | 入库时间 |

当前 5 个环境变量：

| canonical_name | 源变量 | 单位 | 数据源 |
|---|---|---|---|
| sst_c | thetao | °C | CMEMS Physics |
| chl_mg_m3 | chl | mg m-3 | CMEMS BGC |
| npp_mg_c_m3_day | nppv | mg C m-3 day-1 | CMEMS BGC |
| ssh_m | zos | m | CMEMS Physics |
| ssha_m | sla | m | CMEMS DUACS |

五个环境变量在 14,400 个网格月中全部有效，共 72,000 行。sst_c 是最浅模型层海温代理；npp_mg_c_m3_day 是体积 NPP；ssh_m 与 ssha_m 分开保存。

### 2.4 模型特征

feature_definition 保存特征公式、单位、依赖变量、空间窗口、缺测规则和版本。当前 feature_value 已计算 10 个特征：

| field_name | 公式/来源 | 单位 | 当前有效量 |
|---|---|---|---:|
| sst_c | 对齐后的 thetao | °C | 14,400 |
| chl_mg_m3 | 对齐后的 chl | mg m-3 | 14,400 |
| log_chl | log1p(chl_mg_m3) | 1 | 14,400 |
| npp_mg_c_m3_day | 对齐后的 nppv | mg C m-3 day-1 | 14,400 |
| ssh_m | 对齐后的 zos | m | 14,400 |
| ssha_m | 对齐后的 sla | m | 14,400 |
| sst_grad_c_per_100km | 0.1°局地中心差分，归一到 100 km | °C / 100 km | 13,224 |
| ssh_grad_m_per_100km | 0.1°局地中心差分，归一到 100 km | m / 100 km | 13,224 |
| sst_mof_c_per_100km | n=5 多尺度 Sobel，约 100 km | °C / 100 km | 9,000 |
| ssh_mof_m_per_100km | n=5 多尺度 Sobel，约 100 km | m / 100 km | 9,000 |

梯度和中尺度特征在边界格点保留 NULL，并写入 boundary_or_missing，没有用 0 伪造。它们是连续环境特征代理，不表示数据库已经拥有独立的锋面折线数据。

model_run 和 prediction_result 已建表但为空。后续随机森林、XGBoost 或其他模型训练时，只需要登记模型运行、特征集版本、数据指纹、验证方式、指标和预测结果。

## 3. 数据库表结构

| 表 | 用途 | 当前行数 |
|---|---|---:|
| database_metadata | 数据库版本、样例 ID、校验值 | 6 |
| data_source | 数据集级来源和版本 | 4 |
| source_asset | 具体文件/对象资产 | 4 |
| variable_definition | CMEMS 原始变量定义 | 5 |
| grid_definition | 目标网格定义 | 1 |
| grid_cell | 网格空间维度 | 2,400 |
| time_dimension | 月份和时间维度 | 6 |
| environment_observation | 环境长表 | 72,000 |
| effort_observation | GFW 原始努力量 | 7,513 |
| effort_cell_period | 网格月努力量聚合层 | 14,400 |
| alignment_run | 对齐运行和参数追溯 | 1 |
| feature_definition | 模型特征定义 | 10 |
| feature_value | 网格月特征值 | 144,000 |
| model_run | 模型运行元数据 | 0 |
| prediction_result | 预测结果 | 0 |

## 4. 主键和索引

### 主键

- data_source.source_id
- source_asset.asset_id
- variable_definition.variable_id
- grid_definition.grid_id
- grid_cell.cell_id
- time_dimension.time_id
- environment_observation(source_asset_id, variable_id, cell_id, time_id, depth_m)
- effort_observation(dataset_version_id, time_id, cell_id, flag_code, gear_type)
- effort_cell_period(dataset_version_id, time_id, cell_id, target_scope)
- alignment_run.run_id
- feature_definition.feature_id
- feature_value(run_id, feature_id, time_id, cell_id, target_scope)
- model_run.model_run_id
- prediction_result(model_run_id, forecast_origin, target_time_id, cell_id, target_scope)

### 已建立的二级索引

| 索引 | 表 | 用途 |
|---|---|---|
| idx_environment_time_cell_variable | environment_observation | 按月份、网格、变量取环境值 |
| idx_environment_variable_time_cell | environment_observation | 按变量和时间批量取栅格 |
| idx_effort_time_cell_gear_flag | effort_observation | 按月份/网格筛选渔具和船旗 |
| idx_effort_dataset_time_cell | effort_observation | 按数据版本、月份和网格查询原始努力量 |
| idx_effort_cell_period_time_cell_scope | effort_cell_period | API 按月份、网格和目标口径查询 |
| idx_feature_run_time_cell_feature | feature_value | 模型运行按月份、网格、特征读取 |
| idx_grid_cell_grid_lon_lat | grid_cell | 按网格和经纬度范围定位 |
| idx_time_year_month | time_dimension | 按年/月查询时间维度 |

已验证查询计划会命中 idx_effort_cell_period_time_cell_scope，不会扫描整张聚合表。

SQLite 的外键约束是连接级开关。所有服务连接数据库时必须先执行 PRAGMA foreign_keys = ON；构建脚本和验证脚本已经这样设置。

## 5. 重新构建和验证

在项目根目录运行：

    python -m tools.data.build_database --config config/patagonia_2012_h1.json --output data/database/fisheries_forecast.sqlite3

脚本会：

1. 读取现有六个月样例；
2. 从 GFW 月度 ZIP 读取区域内原始 flag/geartype 记录；
3. 建立 15 张表和索引；
4. 写入来源、版本、checksum、对齐方法和特征定义；
5. 检查努力量守恒、主键唯一性、环境覆盖和特征记录数量；
6. 输出表行数、索引和 EXPLAIN QUERY PLAN。

当前数据库文件约 85 MB。原始 CSV、NetCDF 和 ZIP 不复制进 SQLite，只在 source_asset 中保存路径、版本和校验信息；这避免数据库膨胀，也保留了完整追溯链。

## 6. 与主项目的解耦方式

主项目应通过数据服务访问以下逻辑资源：

    /api/v1/catalog
    /api/v1/effort
    /api/v1/environment
    /api/v1/features
    /api/v1/predictions
    /api/v1/dictionary

主项目只依赖返回字段、单位、空值状态和版本信息。以后把 SQLite 换成 PostgreSQL/PostGIS，或把长表改成按月 Parquet/Zarr，只要 API 契约不变，前端和模型调用方无需跟着修改数据库内部实现。
