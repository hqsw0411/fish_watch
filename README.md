# Fish Watch · 渔场环境与捕捞努力量

一个项目根目录维护后端、React 前端、数据流水线和模型源码。服务器上的历史 `deploy/` 和 `vue-server-v1/` 来源映射见 [迁移记录](docs/migration/README.md)。数据、数据库、模型权重和运行环境独立存储。

**这是独立清理分支，未部署到服务器。** 已补齐前端和模型源码并完成本地构建及离线测试。原服务器已有数据和服务未被替换。

## 目录

| 路径 | 职责 |
| --- | --- |
| `backend/app/` | FastAPI、只读样例查询、温差分析、可选预测接口 |
| `frontend/` | React + TypeScript + Vite；服务器旧名中的 vue 不代表 Vue 框架 |
| `scripts/` | 数据获取、按月构建、发布验证、训练和代码打包 |
| `models/` | 特征与模型实现；权重放在外部 `model-artifacts/` 或指定目录 |
| `config/` | 研究区域、变量和时间范围 |
| `requirements/` | 运行、数据处理、模型和测试依赖 |
| `ops/` | 显式运行的发布监督器；不会自动部署或注册任务 |
| `data-manifests/` | 外部数据的轻量清单，不包含数据正文 |
| `docs/legacy/` | 保留来源说明和历史实验记录，不作为当前操作指南 |

## 本地环境

使用 Python 3.12。创建独立虚拟环境后，按任务安装依赖：

```sh
python -m venv .venv
# 激活当前平台对应的虚拟环境后：
python -m pip install -r requirements/runtime.txt
# 数据准备：requirements/data.txt
# 模型训练/预测：requirements/model.txt
# 全量兼容入口：backend/requirements.txt
```

运行和数值处理的固定版本沿用来源项目；已有模型文件必须匹配原训练环境，兼容性尚未验证。`.env.example` 只是配置参考，应用不会自动读取 `.env`。

## 外部数据与启动

配置环境变量 `FISHERIES_SAMPLE_DIR` 为已验证的发布目录，或包含 `latest.json` 的发布集合；相对路径以项目根目录为基准。可使用 `FISHERIES_CONFIG` 选择 `config/east_asia_2012_2024.json`。

发布目录需有 `manifest.json`、`sample.csv` 和匹配的 `sample.sqlite3`。API 默认拒绝重建索引，启动和查询只读已有索引。仅在独立可写开发副本中可显式设置 `FISHERIES_ALLOW_INDEX_BUILD=1`。

```sh
python scripts/serve.py
```

默认监听 `127.0.0.1:8000`。预测另需 `FISHERIES_MODEL_DIR/model.joblib`；缺少模型或模型依赖时预测接口返回 503，其余接口可使用。

## 前端与验证

使用前端锁文件安装；已在 Node 24.21.0 / npm 11.19.0 上完成 TypeScript 检查和 Vite 生产构建。

```sh
cd frontend
npm ci
npm run build
```

回到项目根目录，安装完整离线测试依赖后运行：

```sh
python -m pip install -r requirements/test.txt
python -m pytest
python scripts/build_regional_dataset.py --config config/east_asia_2012_2024.json --plan
```

测试使用临时合成数据。它们不能证明服务器 2012–2024 数据完整，也不能证明旧模型的预测精度。完整构建及生产数据验证状态见迁移记录。

## 打包与部署

```sh
python scripts/package_code.py --output output/fish-watch-code.tar.gz
# 前端已构建后可附带构建资源：
python scripts/package_code.py --include-built-frontend --output output/fish-watch-release.tar.gz
```

只打包代码、配置、文档和前端资源；不打包数据、数据库或模型权重。已有输出文件不会覆盖。`scripts/package_server.py` 保留为兼容入口。

在完成一致性快照、恢复验证和发布差异审查，并取得服务器写回批准前，不切换生产目录、服务或数据库。操作步骤见 [部署说明](docs/deployment.md)。
