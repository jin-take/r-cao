"use client";

import Link from "next/link";
import { useState } from "react";
import { useMvp } from "@/app/mvp-context";

export default function AgentsPage() {
  const { agents, tasks } = useMvp();
  const [showSubAgents, setShowSubAgents] = useState(false);
  const executives = agents.filter((agent) => agent.agentType === "EXECUTIVE" || agent.agentType === "AUDIT");
  const subAgents = agents.filter((agent) => agent.agentType === "SUB_AGENT" || agent.agentType === "EXPANSION_AGENT");

  return (
    <section className="shell">
      <div className="eyebrow">OWNER CONSOLE / AGENT REGISTRY</div>
      <div className="hero"><div><h1>Agent Registry</h1><p>OwnerはExecutiveを直接管理します。Sub AgentとExpansion AgentはExecutive配下に折りたたみ、必要なときだけ監査可能な詳細へドリルダウンします。</p></div><span className="mode-inline">{executives.length} EXECUTIVE / {subAgents.length} SUB</span></div>
      <div className="registry-banner"><div><b>Identity rule</b><span>すべてのAgentに固有名、Role、Mission、Authority、Prohibited Actions、Reports Toを設定</span></div><div><b>Permission rule</b><span>Agentは自分・他Agentの権限、Reward、Policyを変更できない</span></div></div>
      <div className="agent-grid">{executives.map((agent) => {
        const taskCount = tasks.filter((task) => task.assignedExecutiveAgentId === agent.id).length;
        return <Link href={`/agents/${agent.id}`} className="agent-card" key={agent.id}><div className="agent-card-top"><span className="agent-initial">{agent.name.slice(0, 1)}</span><span className={`status status-${agent.status.toLowerCase()}`}>{agent.status}</span></div><div className="agent-name"><h2>{agent.name}</h2><span>{agent.role}</span></div><p>{agent.mission}</p><div className="agent-stats"><span><b>{taskCount}</b> tasks</span><span><b>v{agent.version}</b> identity</span><span><b>{agent.agentType}</b></span></div><footer><span>Reports to Owner</span><span>View profile →</span></footer></Link>;
      })}</div>
      <div className="subagent-toggle"><button className="secondary-button" type="button" onClick={() => setShowSubAgents((value) => !value)}>{showSubAgents ? "⌃ Hide" : "⌄ Show"} Sub / Expansion Agents</button><span>通常はExecutive viewに集約</span></div>
      {showSubAgents && <div className="subagent-list">{subAgents.map((agent) => <Link href={`/agents/${agent.id}`} className="subagent-row" key={agent.id}><span className="agent-initial small">{agent.name.slice(0, 1)}</span><div><strong>{agent.name}</strong><small>{agent.role} · reports to {agents.find((parent) => parent.id === agent.reportsTo)?.name ?? agent.reportsTo}</small></div><span className="status status-small">{agent.status}</span><span>{agent.mission}</span></Link>)}</div>}
      <p className="notice">Owner-onlyの任命・停止・権限変更はPython Control PlaneのPolicy Engineで再検証され、Audit Logへ追記されます。</p>
    </section>
  );
}
