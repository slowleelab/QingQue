# 贡献指南

感谢你对 SmartCS 的关注！我们欢迎各种形式的贡献——代码、文档、测试、Issue 反馈。

## 开始之前

1. 阅读 [开发指南](docs/development.md) 完成环境搭建
2. 浏览 [架构文档](docs/architecture.md) 了解系统设计
3. 查看已有 [Issues](../../issues) 与标记为 `good first issue` 的任务

## 贡献方式

### 报告 Bug

提交 Issue 时请包含：
- 环境信息（OS、Python 版本、相关中间件版本）
- 最小复现步骤
- 期望行为 vs 实际行为
- 相关日志 / 截图

### 提交代码

1. **Fork** 并克隆仓库
2. 从 `main` 切分支：`git checkout -b feat/xxx`
3. 开发并确保本地检查通过：
   ```bash
   make lint && make type-check && make test
   ```
4. 提交（pre-commit 会自动运行检查）
5. 推送并发起 Pull Request

### 分支与 Commit 约定

- 分支前缀：`feat/`、`fix/`、`refactor/`、`docs/`、`test/`、`chore/`
- Commit message 遵循 [Conventional Commits](https://www.conventionalcommits.org/)：
  ```
  feat: 新增反馈撤销接口
  fix: 修复规则热加载 NameError
  docs: 补充 API 参考的 WS 协议
  ```

## 代码规范

- 遵循 [开发指南](docs/development.md#代码规范) 中的 Ruff / mypy / 格式化约定
- 每个模块以 `from __future__ import annotations` 开头
- 用户可见字符串与 docstring 用中文，标识符用英文
- 新增功能需附带测试，覆盖率不低于 60%

## Pull Request 检查清单

- [ ] `make lint` 无新增错误
- [ ] `make type-check` 通过
- [ ] `make test` 通过（新增逻辑有对应测试）
- [ ] 更新了相关文档（如 API / 配置变更）
- [ ] PR 描述清晰说明改动动机与方案

## 安全相关

如发现安全漏洞，**请勿公开 Issue**，请按 [SECURITY.md](SECURITY.md) 私下报告。

## 行为准则

参与本项目即表示同意遵守 [行为准则](CODE_OF_CONDUCT.md)。
