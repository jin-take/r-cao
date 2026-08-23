import { demoTasks } from "@/data/demo";
import type { TaskState } from "@/domain/model";

const columns: { state: TaskState; label: string }[] = [
  { state: "DRAFT", label: "Draft" },
  { state: "IN_PROGRESS", label: "In progress" },
  { state: "IN_REVIEW", label: "Review" },
  { state: "ACCEPTED", label: "Accepted" },
];

export default function TaskBoard() {
  return (
    <section className="shell">
      <div className="eyebrow">OWNER VIEW / TASKS</div>
      <div className="hero"><div><h1>Task Board</h1><p>正式TaskはOwnerが発行し、Agentは割り当てられた権限内で状態を進めます。</p></div><button className="primary" type="button" disabled>Issue Task · Owner</button></div>
      <div className="board">
        {columns.map((column) => (
          <section className="column" key={column.state}>
            <header><span>{column.label}</span><b>{demoTasks.filter((task) => task.state === column.state).length}</b></header>
            {demoTasks.filter((task) => task.state === column.state).map((task) => (
              <article className="task" key={task.id}><small>{task.id} · D{task.difficulty}</small><h2>{task.title}</h2><p>{task.acceptanceCriteria.join(" / ")}</p><footer><span>{(task.rewardLamports / 1_000_000_000).toFixed(2)} SOL</span><time>{task.deadline}</time></footer></article>
            ))}
          </section>
        ))}
      </div>
      <p className="notice">この画面はPhase 1のUI境界を示すプロトタイプです。書き込みAPIは、認証・永続化・AuditLogのトランザクション実装後に有効化します。</p>
    </section>
  );
}
