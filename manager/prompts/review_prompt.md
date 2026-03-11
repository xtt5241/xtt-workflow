请 review 当前任务对象定义的改动。

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

允许修改路径：
{{allowed_paths}}

禁止修改路径：
{{forbidden_paths}}

必要时先查看：
- `git diff --stat origin/{{base_branch}}...HEAD`
- `git diff origin/{{base_branch}}...HEAD`
- 仓库 profile 指定的高风险路径与验证命令

只输出问题，不直接修复。
重点检查：
- 需求遗漏
- 不必要复杂度
- 测试缺失
- 缓存/构建产物误提交
- 风险点
- 与 AGENTS.md 冲突

输出结构：
- findings
- severity
- missing_tests
- rule_conflicts
