请验证当前任务对象定义的改动是否真的可用。

任务标题：{{title}}
任务类型：{{type}}
任务类别：{{task_kind}}
目标仓库：{{repo}}
基础分支：{{base_branch}}
来源分支：{{source_ref}}

Repo Profile：
{{repo_profile_summary}}

DoD：
{{dod_summary}}

任务目标：
{{goal}}

验收标准：
{{acceptance}}

风险级别：{{risk_level}}
证据要求：
{{evidence_required}}

建议测试层级：{{test_strategy_level}}
测试层级原因：{{test_strategy_reason}}
建议验证命令：
{{test_strategy_commands}}

允许修改路径：
{{allowed_paths}}

禁止修改路径：
{{forbidden_paths}}

必要时先查看：
- `git diff --stat origin/{{base_branch}}...HEAD`
- 仓库 profile 指定的安装 / 测试 / build / smoke test 入口

输出：
- 测试层级
- 实际执行命令
- 复现步骤
- pass_paths
- fail_paths
- merge_decision
- residual_risks
- 缺失的 DoD 条件
