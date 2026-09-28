# 贡献指南

欢迎提交错误修复、可访问性改进、解析兼容性修复和路线扩展。较大的功能建议先通过 Issue 讨论使用场景。

## 开发环境

先按 README 创建 `.venv`。以下为 macOS / Linux 命令；Windows 将 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe`。

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest personal_rail/tests -q
.venv/bin/python -m ruff check personal_rail run.py
.venv/bin/python -m ruff format --check personal_rail run.py
```

测试使用合成数据，无需模型凭据。不要用真实用户数据、密钥、个人路径或查询缓存作为测试附件。

## 提交改动

1. Fork 仓库，创建功能分支。
2. 为行为变化补充测试，更新相关文档。
3. 执行上述检查。
4. 提交 Pull Request，说明问题、最终行为及验证方式。

保持来源日期和实际车站校验、未知价格处理与模型输出校验。新增路线时同时更新端点规则、来源映射、界面选项及测试。不要引入登录抢票、验证码绕过或未授权数据接口。

前端为原生静态文件，修改界面时请检查桌面和移动端。模型配置统一从环境变量或未提交的 `.env` 读取。
