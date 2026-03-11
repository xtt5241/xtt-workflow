Prompt Name: build
Prompt Version: 3

任务标题：{{title}}
任务类型：{{type}}
任务类别：{{task_kind}}
目标仓库：{{repo}}
基础分支：{{base_branch}}

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

Tool Router：
{{tool_router_summary}}

相似实现候选：
{{similar_impl_paths}}

Pattern Finder：
{{pattern_finder_summary}}

建议测试层级：{{test_strategy_level}}
测试层级原因：{{test_strategy_reason}}
建议验证命令：
{{test_strategy_commands}}

改动预算：
{{change_budget}}

允许自动提交：{{allow_auto_commit}}
允许 push：{{allow_push}}
允许 PR：{{allow_pr}}
允许依赖变更：{{allow_dependency_changes}}
允许迁移变更：{{allow_migration}}
允许 CI 变更：{{allow_ci_changes}}
允许部署变更：{{allow_deploy_changes}}
允许跨层重构：{{allow_cross_layer_refactor}}

允许修改路径：
{{allowed_paths}}

禁止修改路径：
{{forbidden_paths}}

要求：
1. 先阅读相关代码并总结
2. 优先按任务指定的测试层级执行，只有在证据不足时才升级测试层级
3. 先给出最小 plan
4. 只做与任务相关的改动
5. 补足必要测试
6. 运行验证命令
7. 输出：
   - changed files
   - summary
   - verification
   - risks

限制：
- 不做无关重构
- 不修改 secrets
- 不修改部署配置，除非任务明确要求
