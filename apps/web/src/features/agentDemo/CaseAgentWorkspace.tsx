import { type ReactNode, useEffect, useState } from "react";
import { AgentDemoPanel } from "./AgentDemoPanel";
import { LiveAgentPanel } from "./LiveAgentPanel";

interface CaseAgentWorkspaceProps {
  caseId: string | null;
  caseTitle: string | null;
  caseType: string | null;
  refreshKey: number;
  onChanged: () => Promise<void>;
}

export function CaseAgentWorkspace(props: CaseAgentWorkspaceProps) {
  const [fakeDemoOpen, setFakeDemoOpen] = useState(false);

  useEffect(() => {
    setFakeDemoOpen(false);
  }, [props.caseId]);

  return (
    <section className="case-agent-workspace" aria-label="案件 Agent 工作区">
      <LiveAgentPanel {...props} />
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
        <small>确定性 Fake Provider · API 与行为保持不变</small>
      </summary>
      {open && children}
    </details>
  );
}
