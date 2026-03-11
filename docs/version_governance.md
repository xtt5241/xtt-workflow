# Version Governance

本文件定义 `xtt-workflow` 的版本留痕约定，目标是让每个任务结果都能追溯“当时到底用了哪套规则”。

## 范围

当前纳入治理的对象：

- `manager/prompts/*.md`
- `config/repos/*.json`
- `config/task_schema.json`
- `config/result_schema.json`
- 目标仓库根目录 `AGENTS.md`（如果存在）

## 约定

- Prompt 文件使用文本声明版本，例如 `Prompt Version: 1`
- JSON 配置文件使用顶层 `version` 字段
- `manager/result_writer.py` 在写入结果时记录：
  - 当前阶段 prompt 版本
  - prompt bundle 全量版本
  - repo profile 版本
  - task schema 版本
  - result schema 版本
  - AGENTS 版本与指纹
  - 每个文件的 `mtime` 与 `sha256`

## Result Payload

`manager/results/<task-id>.json` 中新增这些字段：

- `result_schema_version`
- `prompt_bundle_versions`
- `agents_versions`
- `version_manifest`

其中 `version_manifest` 是统一入口，优先给 UI、复盘和后续统计消费。

## 维护规则

- 修改 prompt 行为时，更新对应 prompt 顶部版本号
- 修改 repo profile 协议时，更新对应 repo profile 的 `version`
- 修改结果结构时，同时更新 `config/result_schema.json` 的 `version`
- 修改任务结构时，更新 `config/task_schema.json` 的 `version`
