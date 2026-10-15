import { useEffect, useState, type ReactNode } from "react";

export type WorkflowArea = "overview" | "agent" | "review" | "actions" | "records";

interface CaseWorkflowProps {
  caseTitle: string;
  caseStatus: string;
  caseStatusCode: string;
  nextStepTitle: string;
  nextStepDetail: string;
  primaryActionLabel: string;
  onPrimaryAction: () => void;
  onReturn: () => void;
  recommendation?: ReactNode;
  operations?: ReactNode;
  feedback?: ReactNode;
  activeArea: WorkflowArea;
  onSelect: (area: WorkflowArea) => void;
  overview: ReactNode;
  agent: ReactNode;
  review: ReactNode;
  actions: ReactNode;
  records: ReactNode;
}

const WORKFLOW_AREAS: Array<{
  id: WorkflowArea;
  label: string;
}> = [
  { id: "overview", label: "案件概览" },
  { id: "agent", label: "问题分析" },
  { id: "review", label: "事实复核" },
  { id: "actions", label: "行动与截止" },
  { id: "records", label: "记录与导出" },
];

export function CaseWorkflow({
  caseTitle,
  caseStatus,
  caseStatusCode,
  nextStepTitle,
  nextStepDetail,
  primaryActionLabel,
  onPrimaryAction,
  onReturn,
  recommendation,
  operations,
  feedback,
  activeArea,
  onSelect,
  overview,
  agent,
  review,
  actions,
  records,
}: CaseWorkflowProps) {
  const [actionsVisited, setActionsVisited] = useState(activeArea === "actions");
  useEffect(() => {
    if (activeArea === "actions") setActionsVisited(true);
  }, [activeArea]);

  const panels: Record<WorkflowArea, ReactNode> = {
    overview,
    agent,
    review,
    actions,
    records,
  };

  return (
    <section className="case-workflow" aria-labelledby="case-workflow-title">
      <div className="case-control">
        <div className="case-control-identity">
          <span className={`case-state case-state-${caseStatusCode}`}>{caseStatus}</span>
          <div>
            <small>当前案件</small>
            <h2 id="case-workflow-title">{caseTitle}</h2>
          </div>
        </div>
        {recommendation ?? <><div className="case-control-next">
          <small>推荐步骤</small>
          <strong>{nextStepTitle}</strong>
          <span>{nextStepDetail}</span>
        </div>
        <div className="case-control-actions">
          <button type="button" className="secondary-action" onClick={onReturn}>
            返回案件列表
          </button>
          <button type="button" onClick={onPrimaryAction}>
            {primaryActionLabel}
          </button>
        </div></>}
        {operations}
      </div>
      <div className="workflow-area-tabs" role="tablist" aria-label="案件工作区域">
        {WORKFLOW_AREAS.map((area) => (
          <button
            type="button"
            role="tab"
            id={`workflow-tab-${area.id}`}
            aria-controls={`workflow-panel-${area.id}`}
            aria-selected={activeArea === area.id}
            className={activeArea === area.id ? "is-active" : undefined}
            key={area.id}
            onClick={() => onSelect(area.id)}
          >
            <strong>{area.label}</strong>
          </button>
        ))}
      </div>
      <label className="workflow-area-select">
        当前工作区域
        <select value={activeArea} onChange={(event) => onSelect(event.target.value as WorkflowArea)}>
          {WORKFLOW_AREAS.map((area) => (
            <option value={area.id} key={area.id}>{area.label}</option>
          ))}
        </select>
      </label>
      {feedback}
      {activeArea !== "actions" && <div
          key="active"
          className="workflow-area-panel"
          role="tabpanel"
          id={`workflow-panel-${activeArea}`}
          aria-labelledby={`workflow-tab-${activeArea}`}
        >
          {panels[activeArea]}
        </div>}
      {(actionsVisited || activeArea === "actions") && <div
          key="actions"
          className="workflow-area-panel"
          role="tabpanel"
          id="workflow-panel-actions"
          aria-labelledby="workflow-tab-actions"
          hidden={activeArea !== "actions"}
        >
          {actions}
        </div>}
    </section>
  );
}
