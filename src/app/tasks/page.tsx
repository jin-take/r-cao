"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useMvp } from "@/app/mvp-context";
import { formatSol } from "@/data/mvp";
import type { MvpTask, MvpTaskStatus } from "@/domain/model";

const columns: { status: MvpTaskStatus; label: string }[] = [
  { status: "DRAFT", label: "Draft" },
  { status: "APPROVED", label: "Approved" },
  { status: "PLANNING", label: "Planning" },
  { status: "IN_PROGRESS", label: "In progress" },
  { status: "REVIEW", label: "Review" },
  { status: "AUDIT", label: "Audit" },
  { status: "OWNER_REVIEW", label: "Owner review" },
  { status: "REWORK", label: "Rework" },
  { status: "BLOCKED", label: "Blocked" },
  { status: "COMPLETED", label: "Completed" },
  { status: "REJECTED", label: "Rejected" },
  { status: "CANCELLED", label: "Cancelled" },
];

function TaskCard({ task, agentName }: { task: MvpTask; agentName: string }) {
  return (
    <Link className="task" href={`/tasks/${task.id}`}>
      <small>{task.id} · {task.priority} · {task.riskLevel} risk</small>
      <h2>{task.title}</h2>
      <p>{task.objective}</p>
      <div className="progress"><span style={{ width: `${task.progress}%` }} /></div>
      <footer><span>{formatSol(task.rewardBudgetLamports)} budget</span><span>{agentName}</span></footer>
    </Link>
  );
}

export default function TaskBoard() {
  const { tasks, agents, createTask } = useMvp();
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [deadline, setDeadline] = useState("2026-09-30");
  const [budget, setBudget] = useState("0.50");
  const [executive, setExecutive] = useState("agent-theo");
  const executives = agents.filter((agent) => agent.agentType === "EXECUTIVE");

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!title.trim() || !objective.trim()) return;
    createTask({
      title: title.trim(),
      objective: objective.trim(),
      deadline,
      rewardBudgetLamports: Math.round(Number(budget) * 1_000_000_000),
      assignedExecutiveAgentId: executive,
    });
    setTitle("");
    setObjective("");
    setShowForm(false);
  };

  const nameOf = (id: string) => agents.find((agent) => agent.id === id)?.name ?? id;

  return (
    <section className="shell wide-shell">
      <div className="eyebrow">OWNER CONSOLE / TASK BOARD</div>
      <div className="hero">
        <div><h1>Task Board</h1><p>正式TaskはOwnerが発行し、Executiveが計画・委任・実行します。Reward Budgetは後払い評価の上限であり、自動支払いではありません。</p></div>
        <button className="primary" type="button" onClick={() => setShowForm((open) => !open)}>{showForm ? "Close form" : "＋ Issue Owner Task"}</button>
      </div>

      {showForm && <form className="command-form panel" onSubmit={submit}>
        <div className="panelHead"><h2>Issue a draft Task</h2><span>Owner only</span></div>
        <div className="form-grid">
          <label>Title<input required value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Task title" /></label>
          <label>Executive<select value={executive} onChange={(event) => setExecutive(event.target.value)}>{executives.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} · {agent.role}</option>)}</select></label>
          <label className="span-2">Objective<textarea required value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="What must be achieved?" rows={2} /></label>
          <label>Deadline<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
          <label>Reward Budget (SOL)<input type="number" min="0" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} /></label>
        </div>
        <div className="form-actions"><span className="muted">Creates DRAFT only. Approval and completion remain separate Owner decisions.</span><button className="primary" type="submit">Create Draft</button></div>
      </form>}

      <div className="board board-expanded">
        {columns.map((column) => {
          const columnTasks = tasks.filter((task) => task.status === column.status);
          return <section className="column" key={column.status}>
            <header><span>{column.label}</span><b>{columnTasks.length}</b></header>
            {columnTasks.map((task) => <TaskCard key={task.id} task={task} agentName={nameOf(task.assignedExecutiveAgentId)} />)}
            {columnTasks.length === 0 && <div className="empty">No tasks</div>}
          </section>;
        })}
      </div>
      <div className="board-legend"><span><i className="dot green" />Owner command</span><span><i className="dot amber" />Approval required</span><span><i className="dot red" />Blocked / rejected</span></div>
    </section>
  );
}
