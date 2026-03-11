# Gitignore Baseline

这是一份给业务 repo 使用的 `.gitignore` 基线建议，目的不是“一把梭全部复制”，而是给 `xtt-workflow` 管理下的项目提供一个安全起点，降低 builder 把缓存、构建产物和临时文件误提交的概率。

## 通用建议

优先保留这类规则：

```gitignore
# OS / editor
.DS_Store
.vscode/
.idea/

# temp
*.tmp
*.temp
tmp/
temp/

# logs
*.log

# build outputs
dist/
build/
coverage/
htmlcov/
```

## Python 项目

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.hypothesis/
.tox/
.nox/
.coverage
coverage.xml
.venv/
venv/
```

## Node / Web 项目

```gitignore
node_modules/
.next/
.nuxt/
.svelte-kit/
.parcel-cache/
.turbo/
*.tsbuildinfo
.eslintcache
```

## 使用建议

- 如果 repo 已经有 `.gitignore`，优先合并而不是覆盖
- 如果 repo 会生成静态构建目录，通常应忽略 `dist/`、`build/`、`coverage/`
- 如果 repo 必须跟踪某些生成文件，应明确在 repo profile 或文档里说明，避免被当成误提交产物
- builder 现在会在提交前清理常见缓存/产物，但 `.gitignore` 仍然是第一道防线
