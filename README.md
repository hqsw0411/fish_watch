# Fish Watch · 渔场环境与捕捞努力量

统一维护 Web API、React 前端、数据流水线和模型源码。数据、数据库及模型权重留在外部存储；版本由 Git 管理，不再保留 v1/new/week 命名的整套代码副本。

## 从哪里开始

| 要做的事 | 位置 |
| --- | --- |
| 改接口、查询或温差分析 | `backend/app/` |
| 改界面、地图和样式 | `frontend/` |
| 获取、构建、验证数据 | `tools/data/` |
| 训练或评估模型 | `tools/model/`；实现位于 `models/` |
| 检查部署、打包代码 | `tools/release/` |
| 配置 Linux/Slurm 运行方式 | `deploy/` |
| 选择区域和年份 | `config/` |
| 安装依赖 | `requirements/` |
| 运行验证 | `tests/` |

完整目录树及命令索引见 [项目结构](docs/structure.md)。[数据库设计](docs/database.md)、[模型工作流](docs/modeling.md)、[部署与回滚](docs/deployment.md) 分别说明各自职责。[迁移记录](docs/migration/README.md) 保留源码来源和验证边界。

## 安装与开发

从项目根目录运行以下命令，使用 Python 3.12：

```sh
python -m venv .venv
# 激活当前平台的虚拟环境后：
python -m pip install -r requirements/runtime.txt
```

数据处理安装 `requirements/data.txt`，模型工具安装 `requirements/model.txt`，完整离线测试安装 `requirements/test.txt`。不再通过 backend/ 或 deploy/ 下的重复依赖文件转发。

配置 `FISHERIES_SAMPLE_DIR` 为已验证发布目录或带 latest.json 的发布集合，可用 `FISHERIES_CONFIG` 选择区域配置。发布必须包含 manifest.json、sample.csv 和匹配的 sample.sqlite3。API 默认只读已有索引；仅独立可写开发副本可设置 `FISHERIES_ALLOW_INDEX_BUILD=1`。`.env.example` 是示例，应用不自动加载它。

```sh
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

前端使用锁文件构建，已在 Node 24.21.0 / npm 11.19.0 下验证：

```sh
cd frontend
npm ci
npm run build
```

## 验证和打包

回到项目根目录：

```sh
python -m pip install -r requirements/test.txt
python -m pytest
python -m tools.data.build_regional_dataset --config config/east_asia_2012_2024.json --plan
python -m tools.release.package_code --output output/fish-watch-code.tar.gz
# 前端构建完成后，可附带构建资源：
python -m tools.release.package_code --include-built-frontend --output output/fish-watch-release.tar.gz
```

命令统一使用模块入口，不依赖 sys.path 补丁。打包排除数据、权重、环境和缓存，缺少关键源码会报错，已有包不会覆盖。

## 运行边界

这是尚未写回服务器的清理分支。测试使用临时合成数据，不能证明生产数据完整或旧模型的精度与环境兼容性。预测功能需要额外安装模型依赖，并配置 `FISHERIES_MODEL_DIR`；缺少模型时接口返回 503。

服务器写回前仍需一致性快照、恢复验证、真实数据/模型核验和具体部署批准。当前服务与数据没有因仓库整理而被覆盖或重启。
