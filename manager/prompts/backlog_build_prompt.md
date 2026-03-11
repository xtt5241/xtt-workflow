任务：{{backlog_item_id}} {{backlog_item_title}}
任务标题：{{title}}
任务类型：{{type}}
任务类别：{{task_kind}}
目标仓库：{{repo}}
基础分支：{{base_branch}}
阶段：{{phase}}

Repo Profile：
{{repo_profile_summary}}

DoD：
{{dod_summary}}

任务目标：
{{goal}}

验收标准：
{{acceptance}}

建议关注文件：
{{files_hint}}

输出偏好：
{{outputs_hint}}

风险级别：{{risk_level}}
证据要求：
{{evidence_required}}

建议测试层级：{{test_strategy_level}}
测试层级原因：{{test_strategy_reason}}
建议验证命令：
{{test_strategy_commands}}

改动预算：
{{change_budget}}

允许自动提交：{{allow_auto_commit}}
允许 push：{{allow_push}}
允许 PR：{{allow_pr}}

允许修改路径：
{{allowed_paths}}

禁止修改路径：
{{forbidden_paths}}

要求：
1. 先阅读相关代码并总结
2. 优先按任务指定的测试层级执行，只有在证据不足时才升级测试层级
3. 先给出最小 plan
4. 只做与当前 backlog item 直接相关的改动
5. 保持改动最小，不做无关重构
6. 运行必要验证
7. 输出：
   - changed files
   - summary
   - verification
   - risks

限制：
- 不修改 secrets
- 不修改部署配置，除非 backlog item 明确要求
- 不超出任务边界
