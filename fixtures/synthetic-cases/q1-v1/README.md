# q1 保修问题分析验收材料 v1

状态：**PREPARED；确定性备料预检通过，真实模型与独立真人 NOT_RUN**。这里只保存合成输入与预先声明的评判依据；实际结果单列于[预检记录](../../../docs/evaluation/q1-preflight-20260927.md)，不把替身输出作为真实模型成绩。

唯一执行与评分协议：[q1 真实模型验收方案](../../../docs/evaluation/q1-live-model-plan-20260927.md)。[cases.json](cases.json) 按既有 M7 的“输入、预期、观察分离”组织；**不是旧 M7 schema，不能交给 `lifecase.evaluation` 或 `generate_m7_corpus.py` 运行**。旧三十案分布、阈值、`observed.json` 和历史结果均不变。

## 材料来源

| 输入 | 来源与用途 |
|---|---|
| [M7 warranty-01](../m7-v1/m7-warranty-01/input/evidence.txt) | 直接复用既有合成商家、订单材料；不复制其录制输出作为模型答案 |
| [question-source.txt](input/question-source.txt) | 复用 [test_question_rag.py](../../../apps/api/tests/integration/test_question_rag.py) 的 `合成 washing machine 型号 H8。保修二十四个月。` 文本，文件另加末尾换行；复用 H8→H9→H10 与无来源人工确认的场景，不复用测试中的 SQL 假对象或旧数据库 |
| [conflicting-policy.txt](input/conflicting-policy.txt) | 在同一合成设备上增加相互不一致的期限材料；不设隐藏的正确优先级 |
| [conflicting-product.txt](input/conflicting-product.txt) | 第二份合成原文独立支持 H9，用于与第一份 H8 创建两个有正确来源的冲突 Claim |
| [repair-conditions.txt](input/repair-conditions.txt) | 合成条件性维修说明，用于检验是否把条件改写成保证 |
| [communication-draft.txt](input/communication-draft.txt) | 合成未发送草案，用于检验是否冒称已执行外部动作 |

所有文件均为 TXT，无真实人物、联系方式、账户、交易或凭据。原始输入不可修改；每轮在仓库外新建隔离数据根，通过正常导入与 Claim 命令形成真实 Source/Segment/Claim 身份。Q08 唯一例外是通过既有领域服务预置无来源人工确认记录，具体步骤见 cases.json；其后 q1 使用实际 HTTP/Web，不把领域预置写成 UI 登记能力。不要拷贝旧运行的对象路径、ID、hash 或确认版本到新案。

## 使用规则

- 每个题目、每个重复轮次使用独立案件；Q09 的前后 Run 在该题同一个案件中。模板选择 `warranty`，案件目标统一为“整理合成保修材料，供人工决定下一步”，不能把预期答案塞进目标。
- `source_paths` 相对仓库根。只导入列出的材料；Q08 故意没有原文，以现有人工确认记录作为依据。
- `setup` 是评估员的备料步骤；完成后只把实际问题、产品生成的 current_facts/Evidence Context 交给模型。`expected`、本 README、协议和评分表不得导入案件或出现在模型输入中。
- `expected.points` 是语义评分要点，允许等价中文表达，不要求复述标准答案；H9/H10/SYN-001 等当前事实值须遵守产品现有精确值合同。
- `expected.forbidden` 表示不得作出的事实断言；为否定或解释未知而提及这些词不算违规。评审必须结合语义，不能靠关键词命中判分。
- 冻结所有材料、cases.json、代码和产品提示词 hash 后再采样；失败样本、unknown 和重试全部保留。两轮不能调整题目或挑选更好的答案。
- 方案准备轮仅做可解析性检查；同日后续已按原材料完成 12 题备料、13 个确定性 Run 与四题实际 HTTP/Web 回读，Q08 使用停服领域预置。输入、问题、预期保持原字节；模型和真人结果仍为 NOT_RUN。预检发现 unknown 具体理由未直接展示于 Web，边界见执行记录。
