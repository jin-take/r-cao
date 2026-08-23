import Link from "next/link";
import { demoOperations } from "@/data/operations";

export default function OperationsPage() {
  return (
    <section className="shell">
      <div className="eyebrow">OWNER VIEW / OPERATIONS</div>
      <div className="hero">
        <div>
          <h1>Operations search</h1>
          <p>
            task_id・run_id・message・memory・監査証跡を横断して、Agentの提案と実行結果を検索します。
            権限判定はPython Control Plane側で行います。
          </p>
        </div>
        <Link className="primary" href="/tasks">Back to Task Board</Link>
      </div>
      <form className="searchForm">
        <label>Query<input name="q" placeholder="review, task_id, evidence..." /></label>
        <label>Scope<select name="scope" defaultValue="ALL"><option>ALL</option><option>MESSAGES</option><option>RUNS</option><option>MEMORY</option><option>AUDIT</option></select></label>
        <button className="primary" type="button" disabled>Search · API wiring pending</button>
      </form>
      <div className="operationList">
        {demoOperations.map((operation) => (
          <article className="operation" key={operation.recordId}>
            <header><span className="tag">{operation.scope}</span><time>{operation.createdAt}</time></header>
            <h2>{operation.title}</h2>
            <p>{operation.body}</p>
            <footer><span>task_id: {operation.taskId ?? "—"}</span><span>run_id: {operation.runId ?? "—"}</span><span>{operation.status}</span></footer>
          </article>
        ))}
      </div>
      <p className="notice">表示は監査可能なread modelのプロトタイプです。モデル出力、Tool実行、Agent間メッセージは、task_id・run_id・trace_idを失わない形でPostgreSQLへ保存します。</p>
    </section>
  );
}
