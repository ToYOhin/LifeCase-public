# Contributing

欢迎通过 Issue 或 Pull Request 提交问题、修复和改进建议。

## 开始之前

- 只使用合成或不可逆脱敏的数据；
- 不提交凭据、个人材料、运行数据库、导出包或本机绝对路径；
- 保持模型 Provider 默认关闭，不在测试中调用真实或付费模型；
- 不改变 Case scope、人工确认或外部行动边界，除非变更明确说明风险并包含对应测试。

## 本地验证

```powershell
uv sync --frozen
npm ci
.\scripts\verify.ps1
```

提交应保持范围小、说明清楚，并包含与改动风险相称的测试。UI 变更应同时检查桌面和移动视口。

## Pull Request

Pull Request 请说明：

- 改了什么以及为什么；
- 用户或兼容性影响；
- 执行了哪些验证；
- 是否涉及数据、凭据、网络、Provider 或外部行动边界。
