# xtt Codex Workflow 复用指南

## 1. 目标

这份文档用于把当前这套本地 Codex 自动化工作流，快速复用到另一台机器或另一个环境中。

当前工作流目标：

- 在 Ubuntu VM 内运行所有 Codex worker
- 使用独立 `CODEX_HOME` 隔离不同角色
- 用 tmux 常驻 worker 与 Web Manager
- 用 Git worktree 隔离 build / review / verify 阶段
- 用 Web 页面派单
- 支持多项目切换
- 支持同一中转站地址、多把 key 分配到不同 worker
- 使用方案 A：`builder` 完成后自动 `git commit`，后续 `review` / `verify` 基于任务分支 diff 执行

---

## 2. 当前目录结构

固定根目录：

```text
~/xtt-workflow/
├─ workspace/
│  ├─ repo-main/
│  ├─ <project>-wt-build/
│  ├─ <project>-wt-review/
│  └─ <project>-wt-verify/
├─ manager/
│  ├─ app.py
│  ├─ templates/
│  ├─ static/
│  ├─ workers/
│  ├─ prompts/
│  ├─ queue/
│  ├─ logs/
│  └─ state/
├─ scripts/
├─ codex-homes/
└─ docs/
```

---

## 3. 已落地的关键能力

### 3.1 Worker 隔离

每个角色使用独立 `CODEX_HOME`：

- `planner`
- `builder`
- `reviewer`
- `verifier`

对应目录：

```text
~/xtt-workflow/codex-homes/planner/
~/xtt-workflow/codex-homes/builder/
~/xtt-workflow/codex-homes/reviewer/
~/xtt-workflow/codex-homes/verifier/
```

Builder 固定配置策略：

- `builder` 使用唯一固定策略，不做运行时动态切换
- 当前固定策略版本：`builder-policy-v1`
- 配置入口唯一来源：`scripts/setup_relay_keys.sh`
- 实际落盘文件：`codex-homes/builder/config.toml`
- 固定值：
  - `model_provider = "relay-a"`
  - `model = "gpt-5.4"`
  - `model_reasoning_effort = "high"`
  - `model_verbosity = "high"`
- 原则：
  - builder 质量优先，禁止为了临时省钱/提速随意改成低配
  - 如果将来要升级 builder 模型或推理强度，必须同时修改脚本和文档，而不是只改本机现成 `config.toml`
  - reviewer / verifier / planner 可以按角色单独调优，但 builder 保持唯一基线

### 3.2 多项目支持

Web 管理页支持：

- 选择项目名 `repo`
- 选择基础分支 `base_branch`

系统会自动创建按项目隔离的 worktree：

- `<repo>-wt-build`
- `<repo>-wt-review`
- `<repo>-wt-verify`

### 3.3 方案 A 链路

当前链路是：

1. `builder` 在 build worktree 中完成改动
2. `builder` 自动执行 `git add -A && git commit`
3. `postprocess_done.sh` 生成 `review` 任务，并把 `source_ref` 指向 build 分支
4. `review` 基于 `source_ref` 创建 review worktree，并显式比较 `origin/<base_branch>...HEAD`
5. `verify` 同样基于上游分支 diff 执行验证

这解决了最初“review/verify 看不到 build 改动”的问题。

---

## 4. 首次部署步骤

### 4.1 安装系统依赖

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl wget unzip jq tmux python3 python3-venv python3-pip build-essential ripgrep fd-find rsync nodejs npm
```

### 4.2 安装 Codex CLI

```bash
npm install -g @openai/codex
codex --version
```

### 4.3 创建目录

```bash
mkdir -p ~/xtt-workflow/{workspace,manager/{workers,prompts,queue/{pending,running,done,failed},logs,state,templates,static},scripts,config,codex-homes/{planner,builder,reviewer,verifier},backups,docs}
```

### 4.4 clone 项目仓库

示例：

```bash
cd ~/xtt-workflow/workspace
git clone <YOUR_REPO_URL> repo-main
cd repo-main
git checkout main
git pull --ff-only
```

---

## 5. 中转站 / API Key 配置

### 5.1 当前使用脚本

使用：

```bash
~/xtt-workflow/scripts/setup_relay_keys.sh
```

这个脚本已经改成“顶部写死变量”的形式。

你只需要编辑这几个值：

- `BASE_URL`
- `BUILDER_KEY`
- `REVIEWER_KEY`
- `VERIFIER_KEY`
- `PLANNER_KEY`

执行：

```bash
~/xtt-workflow/scripts/setup_relay_keys.sh --restart-tmux
```

### 5.2 当前绑定关系

- `builder` -> `relay-a`
- `reviewer` -> `relay-b`
- `verifier` -> `relay-c`
- `planner` -> `relay-b`

如果 `PLANNER_KEY` 为空，则默认复用 `REVIEWER_KEY`。

---

## 6. GitHub SSH 配置

### 6.1 当前本机 SSH 配置

当前使用别名 host：

```text
github.com-xtt-workflow
```

对应文件：

- `~/.ssh/config`
- `~/.ssh/id_ed25519_github_xtt_workflow`
- `~/.ssh/id_ed25519_github_xtt_workflow.pub`

### 6.2 远程仓库切换为 SSH

当前仓库已切换为 SSH：

```bash
git -C ~/xtt-workflow/workspace/repo-main remote set-url origin git@github.com-xtt-workflow:<owner>/<repo>.git
```

### 6.3 验证 SSH

```bash
ssh -T git@github.com-xtt-workflow
git -C ~/xtt-workflow/workspace/repo-main fetch origin
```

如果第一条返回：

```text
Hi <user>! You've successfully authenticated, but GitHub does not provide shell access.
```

说明 SSH 已配置成功。

---

## 7. Web 管理页

### 7.1 现状

当前 Web 已升级为 Bootstrap 版本，支持：

- 多项目选择
- 基础分支下拉
- 队列滚动显示
- 最近日志查看
- 失败任务重试

### 7.2 启动方式

```bash
cd ~/xtt-workflow/manager
python3 -m venv .venv
source .venv/bin/activate
pip install flask

tmux new-session -d -s xtt-web "bash -lc 'cd $HOME/xtt-workflow/manager && source .venv/bin/activate && python app.py'"
```

访问：

```text
http://127.0.0.1:8787
```

---

## 8. Worker 启动方式

### 8.1 启动所有 worker

```bash
tmux new-session -d -s xtt-builder "$HOME/xtt-workflow/manager/workers/loop_role.sh builder"
tmux new-session -d -s xtt-reviewer "$HOME/xtt-workflow/manager/workers/loop_role.sh reviewer"
tmux new-session -d -s xtt-verifier "$HOME/xtt-workflow/manager/workers/loop_role.sh verifier"
tmux new-session -d -s xtt-postprocess "$HOME/xtt-workflow/manager/workers/loop_postprocess.sh"
```

### 8.2 查看状态

```bash
tmux ls
```

---

## 9. 如何新增项目

### 9.1 clone 新项目

```bash
cd ~/xtt-workflow/workspace
git clone <YOUR_REPO_URL> my-project
```

### 9.1.1 为新 repo 自动生成初始 profile（推荐）

```bash
~/xtt-workflow/scripts/onboard_repo_profile.sh my-project
```

这个命令会：

- 扫描 `workspace/my-project` 的技术栈、常见命令、目录和风险点
- 生成初始 `config/repos/my-project.json`
- 给后续 `tool_router` / test strategy / risk check 提供更好的默认值

如果你是直接用 Codex 做接入，也可以使用 skill：

```text
$repo-understand
```

让它先阅读仓库并给出 profile 建议，再决定是否写入配置。

如果你把 repo 里的本地 skill 同步到各个 `codex-home`，可以执行：

```bash
~/xtt-workflow/scripts/link_local_skills.sh
```

### 9.2 在管理页创建任务时填写

- `repo = my-project`
- `base_branch = main` 或真实存在的远程分支

注意：

- `repo` 必须是 `workspace/` 下已存在的 Git 仓库目录名
- `base_branch` 必须是远程 `origin/<branch>` 中真实存在的分支

---

## 10. 当前关键脚本说明

### 10.1 创建 worktree

```text
~/xtt-workflow/scripts/create_worktree.sh
```

支持：

- `REPO_MAIN`
- `WORKTREE_PATH`
- `BRANCH_NAME`
- `BASE_BRANCH`
- `SOURCE_REF`

如果提供 `SOURCE_REF`，则优先从该分支/提交创建新 worktree。

### 10.2 执行单任务

```text
~/xtt-workflow/manager/workers/run_one_task.sh
```

职责：

- 选出当前角色可执行任务
- 创建 worktree
- 渲染 prompt
- 调用 `codex exec`
- 处理失败 / 重试
- 如果角色是 `builder`，成功后自动提交改动
- 自动清理 `__pycache__` / `*.pyc`，避免把 Python 产物提交进仓库

### 10.3 后处理任务

```text
~/xtt-workflow/manager/workers/postprocess_done.sh
```

职责：

- `build done` 后自动生成 `review`
- `review done` 后自动生成 `verify`
- 自动传递 `source_ref`

---

## 11. 已解决的关键问题

### 11.1 多 worker 共用配置导致限流

解决方式：

- 每个 worker 独立 `CODEX_HOME`
- 每个角色可绑不同 key
- 同一中转站地址也可拆多 key

### 11.2 管理页太原始

解决方式：

- 重构为 Bootstrap 页面
- 队列和日志使用滚动容器
- 多项目、多分支支持

### 11.3 review / verify 看不到 build 改动

解决方式：

- 使用方案 A
- builder 自动 commit
- review / verify 基于 `source_ref` 创建 worktree
- prompt 明确要求比较 `origin/<base_branch>...HEAD`

### 11.4 任务前置步骤失败会卡住 running

解决方式：

- `run_one_task.sh` 中对 `create_worktree.sh` 和 prompt 渲染加失败兜底
- 失败直接进 `failed`

---

## 12. 当前仍需注意的事项

- `builder` 是真的会改代码并提交的，后续如果要 push / merge，需要你明确决定是否自动化
- `review` 当前是“只输出问题，不修复”
- `verify` 当前是“验证并给可合并建议”，不是自动合并
- 如果项目测试命令不统一，build 结果质量依赖任务标题写得是否明确
- 若你希望真正做到“build 后自动 push，再 review/verify 基于远程分支”，还可以继续增强

---

## 13. 一键复用的推荐顺序

### 13.1 把整个目录复制到新环境

最简单是直接保留：

```text
~/xtt-workflow/
```

然后只替换：

- `workspace/` 下实际项目
- `setup_relay_keys.sh` 中的中转站地址和 key
- GitHub SSH key

### 13.2 重配 key

```bash
vim ~/xtt-workflow/scripts/setup_relay_keys.sh
~/xtt-workflow/scripts/setup_relay_keys.sh --restart-tmux
```

### 13.3 重配 SSH

- 生成新 SSH key
- 加到 GitHub
- 切 `origin` 为 SSH
- 运行 `ssh -T` 和 `git fetch origin`

### 13.4 启动服务

```bash
tmux new-session -d -s xtt-builder "$HOME/xtt-workflow/manager/workers/loop_role.sh builder"
tmux new-session -d -s xtt-reviewer "$HOME/xtt-workflow/manager/workers/loop_role.sh reviewer"
tmux new-session -d -s xtt-verifier "$HOME/xtt-workflow/manager/workers/loop_role.sh verifier"
tmux new-session -d -s xtt-postprocess "$HOME/xtt-workflow/manager/workers/loop_postprocess.sh"
tmux new-session -d -s xtt-web "bash -lc 'cd $HOME/xtt-workflow/manager && source .venv/bin/activate && python app.py'"
```

---

## 14. 可直接交给 Codex 的一键执行提示词

把下面这段直接交给 Codex：

```md
你现在是 xtt-workflow 的自动部署助手。

目标：
在 Ubuntu 环境中把 `~/xtt-workflow` 部署成可运行的本地 Codex workflow，并保持以下能力：
- 多 worker 独立 `CODEX_HOME`
- Web 管理页
- 多项目选择
- 基础分支下拉
- build / review / verify 三阶段队列
- builder 自动 commit
- review / verify 基于任务分支 diff
- tmux 常驻运行

限制：
- 只操作 `~/xtt-workflow`
- 不读取 `.env` / secrets 的具体内容
- 如果缺失 key，只列出缺失项，不伪造
- 不删除现有 repo-main

严格按这个顺序执行：
1. 检查并创建目录结构
2. 检查 `git/jq/tmux/python3/node/npm/codex` 是否可用
3. 检查 `scripts/`、`manager/workers/`、`manager/prompts/`、`manager/templates/`、`manager/static/` 是否齐全
4. 检查 `codex-homes/*/config.toml` 和 `auth.json`
5. 检查 GitHub SSH 是否可用：
   - `~/.ssh/config`
   - SSH key 文件
   - `git -C ~/xtt-workflow/workspace/repo-main remote -v`
   - `git fetch origin`
6. 检查 tmux 会话：
   - xtt-builder
   - xtt-reviewer
   - xtt-verifier
   - xtt-postprocess
   - xtt-web
7. 如缺失则按现有脚本恢复启动
8. 最后输出：
   - created files
   - changed files
   - missing secrets
   - git ssh status
   - running tmux sessions
   - remaining manual steps
```

---

## 15. 验收标准

满足以下条件即可视为“可用”：

1. `codex --version` 成功
2. `ssh -T git@github.com-xtt-workflow` 成功
3. `git -C ~/xtt-workflow/workspace/repo-main fetch origin` 成功
4. `tmux ls` 能看到 worker 和 web 会话
5. `http://127.0.0.1:8787` 能打开
6. 页面可创建 build 任务
7. build 成功后自动 commit
8. review 能看到 `origin/<base_branch>...HEAD` 的真实 diff
9. verify 能基于任务分支给出验证结论
