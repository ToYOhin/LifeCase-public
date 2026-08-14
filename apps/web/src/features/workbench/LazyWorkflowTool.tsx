import { useState, type ReactNode } from "react";

interface LazyWorkflowToolProps {
  title: string;
  hint: string;
  children: ReactNode;
  open?: boolean;
}

export function LazyWorkflowTool({
  title,
  hint,
  children,
  open = false,
}: LazyWorkflowToolProps) {
  const [isOpen, setIsOpen] = useState(open);

  return (
    <details
      className="lazy-workflow-tool"
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary>
        <strong>{title}</strong>
        <span>{hint}</span>
      </summary>
      {isOpen && <div className="lazy-workflow-tool-body">{children}</div>}
    </details>
  );
}
