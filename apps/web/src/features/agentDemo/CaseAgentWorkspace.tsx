import { type ReactNode, useEffect, useState } from "react";
import { AgentDemoPanel } from "./AgentDemoPanel";
import { LiveAgentPanel } from "./LiveAgentPanel";
import type { ActionFollowUpTarget } from "../actions/actionFollowUpState";

interface CaseAgentWorkspaceProps {
  caseId: string | null;
  caseTitle: string | null;
  caseType: string | null;
  refreshKey: number;
  onChanged: () => Promise<void>;
  actionFollowUp?: ActionFollowUpTarget | null;
  onOpenAction?: (caseId: string, actionId: string) => void;
}

export function CaseAgentWorkspace(props: CaseAgentWorkspaceProps) {
  const [fakeDemoOpen, setFakeDemoOpen] = useState(false);

  useEffect(() => {
    setFakeDemoOpen(false);
  }, [props.caseId]);

  return (
    <section className="case-agent-workspace" aria-label="案件 Agent 工作区">
      <LiveAgentPanel key={props.caseId} {...props} />
      <FakeDemoDrawer open={fakeDemoOpen} onToggle={setFakeDemoOpen}>
        <AgentDemoPanel {...props} />
      </FakeDemoDrawer>
    </section>
  );
}

interface FakeDemoDrawerProps {
  open: boolean;
  onToggle: (open: boolean) => void;
  children: ReactNode;
}

export function FakeDemoDrawer({ open, onToggle, children }: FakeDemoDrawerProps) {
  return (
    <details
      className="fake-demo-drawer"
      open={open}
      onToggle={(event) => onToggle(event.currentTarget.open)}
    >
      <summary>
        <span>离线开发演示</span>
      </summary>
      {open && <>
        <p>开发用固定示例（Fake Provider），不代表真实模型分析结果。</p>
        {children}
      </>}
    </details>
  );
}
