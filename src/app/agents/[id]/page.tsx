"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMvp } from "@/app/mvp-context";
import { formatSol } from "@/lib/console-utils";

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { agents, tasks, subtasks, setAgentStatus } = useMvp();
  const agent = agents.find((item) => item.id === id);
  if (!agent) return <section className="shell"><div className="eyebrow">AGENT NOT FOUND</div><h1>Unknown Agent</h1><Link className="text-link" href="/agents">Back to Registry</Link></section>;

  const agentName = (agentId: string) => agents.find((item) => item.id === agentId)?.name ?? agentId;

  const assignedTasks = tasks.filter((task) => task.assignedExecutiveAgentId === agent.id);
  const assignedSubtasks = subtasks.filter((task) => task.assignedAgentId === agent.id);

  return <section className="shell">
    <div className="eyebrow"><Link href="/agents">AGENT REGISTRY</Link> / {agent.name}</div>
    <div className="detail-hero agent-detail-hero"><div><div className="agent-card-top"><span className="agent-initial">{agent.name.slice(0, 1)}</span><span className={`status status-${agent.status.toLowerCase()}`}>{agent.status}</span></div><h1>{agent.name}</h1><div className="detail-kicker">{agent.role} · {agent.agentType}</div><p>{agent.mission}</p></div><div className="detail-actions"><div className="identity-chip"><span>Identity version</span><b>v{agent.version}</b><small>{agent.capabilityHash}</small></div>{agent.status === "ACTIVE" ? <button className="danger-button" type="button" onClick={() => void setAgentStatus(agent.id, "SUSPENDED", "Owner stopped Agent from Console")}>Stop Agent</button> : agent.status === "SUSPENDED" && <button className="secondary-button" type="button" onClick={() => void setAgentStatus(agent.id, "ACTIVE", "Owner resumed Agent from Console")}>Resume Agent</button>}</div></div>
    <div className="detail-grid">
      <article className="panel"><div className="panelHead"><h2>Agent Charter</h2><span>auditable</span></div><dl className="facts"><div><dt>Reports To</dt><dd>{agent.reportsTo === "owner-local" ? "Owner" : agentName(agent.reportsTo)}</dd></div><div><dt>Model</dt><dd>{agent.model}</dd></div><div><dt>Budget Limit</dt><dd>{formatSol(agent.budgetLimitLamports)}</dd></div><div><dt>Capability Hash</dt><dd><code>{agent.capabilityHash}</code></dd></div></dl></article>
      <article className="panel"><div className="panelHead"><h2>Responsibilities</h2><span>{agent.responsibilities.length}</span></div><ul className="check-list">{agent.responsibilities.map((item) => <li key={item}><span>✓</span>{item}</li>)}</ul><h3>Authority</h3><ul className="plain-list">{agent.authority.map((item) => <li key={item}>{item}</li>)}</ul></article>
      <article className="panel"><div className="panelHead"><h2>Prohibited Actions</h2><span>hard boundary</span></div><ul className="prohibited-list">{agent.prohibitedActions.map((item) => <li key={item}>DENY <span>{item}</span></li>)}</ul></article>
      <article className="panel"><div className="panelHead"><h2>Assigned Work</h2><span>{assignedTasks.length + assignedSubtasks.length} items</span></div>{assignedTasks.map((task) => <Link className="compact-link" href={`/tasks/${task.id}`} key={task.id}><span>{task.id}</span><strong>{task.title}</strong><em>{task.status}</em></Link>)}{assignedSubtasks.map((task) => <div className="compact-link" key={task.id}><span>{task.id}</span><strong>{task.title}</strong><em>{task.status}</em></div>)}{assignedTasks.length + assignedSubtasks.length === 0 && <p className="muted">No assigned work.</p>}</article>
    </div>
  </section>;
}
