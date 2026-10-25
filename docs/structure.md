# 项目结构与命令索引

所有 Python 命令从项目根目录运行。业务代码不修改 `sys.path`，依赖只由当前虚拟环境提供。

```text
backend/app/          Web API、发布路径解析、只读索引与温差分析
frontend/             React 界面、样式、地图资源和构建配置
models/               特征处理与随机森林实现，不存放训练权重
tools/
  data/               获取数据、构建样例/研究库、发布和校验
  model/              训练与评估命令
  release/            部署检查与代码打包
  paths.py            工具共用的项目根目录和 JSON 转换
deploy/
  linux/              启动、环境探测、ACS 数据任务辅助脚本
  slurm/              显式提交的数据构建作业模板
  supervise.py        发布监督器；只有显式执行才会启动/切换 API
config/               三个有明确区域和年份含义的数据配置
requirements/         runtime / data / model / test 四组依赖
tests/                所有单元与离线集成测试
data-manifests/        外部数据清单，不保存数据正文
docs/                 使用、数据设计、部署与迁移记录
```

## 按任务选择命令

| 任务 | 命令 |
| --- | --- |
| 本地 API | `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000` |
| 查看多年数据计划（不下载） | `python -m tools.data.build_regional_dataset --config config/east_asia_2012_2024.json --plan` |
| 获取捕捞努力量 | `python -m tools.data.fetch_effort --help` |
| 获取环境数据 | `python -m tools.data.fetch_environment --help` |
| 构建样例 | `python -m tools.data.build_sample --help` |
| 构建研究数据库 | `python -m tools.data.build_database --help` |
| 校验东亚完整发布 | `python -m tools.data.validate_surface_release` |
| 模型训练/评估 | `python -m tools.model.train_random_forest --help` / `python -m tools.model.evaluate_random_forest --help` |
| 部署文件检查 | `python -m tools.release.check_deployment --help` |
| 代码打包 | `python -m tools.release.package_code --output output/fish-watch-code.tar.gz` |
| 全部离线测试 | `python -m pytest` |

下载、构建和训练命令会写入指定数据/产物目录，应在独立工作区执行。生产服务写回仍遵循部署审批流程。

## 配置选择

- `patagonia_2012_h1.json`：原始巴塔哥尼亚半年样例，合并了内容相同的 sample_region/week2_region 配置。
- `east_asia_2012_pilot.json`：东亚 2012 年试运行。
- `east_asia_2012_2024.json`：东亚 2012–2024 年完整构建配置。

原始数据路径保留，以免改名切断已有数据引用。配置中的范围是构建要求，不等于已验证完成的覆盖范围。

## 放置新代码的规则

可复用 API 逻辑放在 `backend/app/`，模型实现放在 `models/`；一次性执行的命令按任务归入 `tools/`。测试只放在 `tests/`，平台命令只放在 `deploy/`。不要再增加带 v1/new/week 编号的整套源码副本，版本交给 Git 管理。

数据、数据库、权重、虚拟环境、日志和构建产物不提交。历史移除内容可从基线提交 `170002e` 恢复，具体路径变更见 [结构变更清单](migration/structure-changes.json)。
