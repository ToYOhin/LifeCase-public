export const casePhases = [
  "材料导入",
  "事实确认",
  "缺件诊断",
  "行动审批",
  "等待结果",
  "证据归档",
] as const;

export type CasePhase = (typeof casePhases)[number];
