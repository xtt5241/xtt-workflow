# Git Governance

本文件定义 `xtt-workflow` 当前的 Git 治理规则，目标是让自动化修改在可控边界内进行，并把 push / merge 明确留给单独 gate。

## 1. 分支规则

- builder 只在任务分支上工作，例如 `feat/...`
- review 只在 `review/...` 分支上检查 builder 结果
- verify 只在 `verify/...` 分支上验证实际可用性
- `scripts/create_worktree.sh` 每次都会先清理旧 worktree，再删除同名本地分支并重新创建，避免静默复用旧 branch 状态

## 2. Retry 规则

- 自动 retry（如 429 / rate limit）不会继续复用旧 branch
- 手动 retry `/retry/<name>` 也不会复用旧 branch
- retry 会生成带 `-retry-<n>-<timestamp>` 后缀的新 branch 名，方便追踪每次尝试
- 如果必须复用旧 branch，必须显式做 reset；当前默认策略是不复用

## 3. Diff 基线规则

- review / verify 的统一基线是 `origin/<base_branch>...HEAD`
- worker 在 reviewer / verifier 执行前，会先把 `git diff --stat origin/<base_branch>...HEAD` 和 `git diff --name-only origin/<base_branch>...HEAD` 写入日志
- prompt 也会再次要求 reviewer / verifier 依据同一基线检查，避免误看工作区或误看本地 main

## 4. Push / PR Gate

- verify 成功后不会自动 push
- verify 成功后不会自动 merge
- verify 成功后会进入单独 gate：
  - `manager/queue/ready-to-push/`
  - 或 `manager/queue/ready-to-pr/`
- 当前默认策略是：
  - `allow_push=true` → 进入 `ready-to-push`
  - 否则 → 进入 `ready-to-pr`
- 这些 gate 目录只表达“已具备人工决策条件”，不代表已经执行远程操作

## 5. 禁止事项

- 未经过人工 gate，不允许自动 push / merge
- 不允许静默复用旧 retry branch
- 不允许 review / verify 以错误基线判断“通过”

## 6. 当前落地点

- branch / retry / diff 基线：`manager/workers/run_one_task.sh`
- verify 后 gate：`manager/workers/postprocess_done.sh`
- worktree / branch 创建：`scripts/create_worktree.sh`
- 任务允许 push / PR 的开关：`config/task_schema.json`
