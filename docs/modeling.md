# 模型工作流

`models/features.py` 定义允许使用的空间、月份周期和环境特征；`models/random_forest.py` 实现训练与模型保存。训练入口与模型实现分开，均不包含权重文件。

```sh
python -m pip install -r requirements/model.txt
python -m tools.model.train_random_forest --help
python -m tools.model.evaluate_random_forest --help
```

研究库可用 `FISHERIES_RESEARCH_DB` 或 `--db_path` 指定；训练输出用 `FISHERIES_MODEL_RUNS_DIR` 指定，默认在不入 Git 的 `model-artifacts/runs/`。API 的 `FISHERIES_MODEL_DIR` 指向具体模型目录。

当前接口使用当月环境与位置特征估算表观捕捞时长，是回顾性 nowcast，不能据此声称未来预测性能。训练用 log1p 目标，API 按模型记录的列名取值并只做一次 expm1 还原。缺失目标与零捕捞分开处理，hours 等同源字段不作为模型特征。

切分可用 auto/manual/none，具体参数见命令帮助。全量训练 none 没有独立测试集，不能提供泛化效果结论。每次结果以该次 metrics.json 为准；旧实验数值保留在 Git 历史，不复制到新运行报告中。

已有权重仍需原训练环境和真实数据复核。当前测试验证保存/加载协议和入口功能，没有重新验证生产模型精度。
