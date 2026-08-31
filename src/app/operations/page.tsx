"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useMvp } from "@/app/mvp-context";
import type { OperationScope } from "@/data/operations";

export default function OperationsPage() {
  const { operations, operationsLoading, operationsError, searchOperations } = useMvp();
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<OperationScope>("ALL");

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void searchOperations(query, scope);
  };

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
      <form className="searchForm" onSubmit={submit}>
        <label>Query<input name="q" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="review, task_id, evidence..." /></label>
        <label>Scope<select name="scope" value={scope} onChange={(event) => setScope(event.target.value as OperationScope)}><option>ALL</option><option>TASKS</option><option>MESSAGES</option><option>RUNS</option><option>EVIDENCE</option><option>MEMORY</option><option>AUDIT</option></select></label>
        <button className="primary" type="submit" disabled={operationsLoading}>{operationsLoading ? "Searching…" : "Search"}</button>
      </form>
      {operationsError && <div className="console-banner danger"><b>Operations search failed</b><span>{operationsError}</span></div>}
      <div className="operationList">
        {operations.map((operation) => (
          <article className="operation" key={operation.recordId}>
            <header><span className="tag">{operation.scope}</span><time>{operation.createdAt}</time></header>
            <h2>{operation.title}</h2>
            <p>{operation.body}</p>
            <footer><span>task_id: {operation.taskId ?? "—"}</span><span>run_id: {operation.runId ?? "—"}</span><span>{operation.status}</span></footer>
          </article>
        ))}
      </div>
      {operations.length === 0 && !operationsLoading && <div className="empty-state"><strong>No operation records found.</strong><span>検索条件を変更するか、PostgreSQLのread modelを確認してください。</span></div>}
      <p className="notice">表示は認証済みControl Planeのread modelです。モデル出力、Tool実行、Agent間メッセージは、task_id・run_id・trace_idを失わない形でPostgreSQLへ保存します。</p>
    </section>
  );
}
