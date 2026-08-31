"use client";

import { FormEvent, useState } from "react";
import { defaultApiBaseUrl } from "@/lib/rcao-api";
import { useMvp } from "@/app/mvp-context";

export function ConnectionPanel() {
  const {
    actor,
    connected,
    loading,
    error,
    session,
    connect,
    disconnect,
  } = useMvp();
  const [open, setOpen] = useState(false);
  const [baseUrl, setBaseUrl] = useState(session?.baseUrl ?? defaultApiBaseUrl());
  const [token, setToken] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      await connect(baseUrl, token);
      setToken("");
      setOpen(false);
    } catch {
      // The provider exposes the sanitized API error next to the form.
    }
  };

  if (connected && actor) {
    return (
      <div className="connection-status">
        <span className="connection-dot" />
        <span>{actor.name} · Owner</span>
        <button className="connection-button" type="button" onClick={() => setOpen((value) => !value)}>
          {open ? "Close" : "Session"}
        </button>
        {open && (
          <div className="connection-popover">
            <p><b>Authenticated Owner</b><small>{actor.actorId} · {actor.phase}</small></p>
            <p className="muted">TokenはsessionStorageにのみ保持し、画面へ表示しません。</p>
            <button className="danger-button" type="button" onClick={disconnect}>Disconnect</button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="connection-status disconnected">
      <span className="connection-dot" />
      <button className="connection-button" type="button" onClick={() => setOpen((value) => !value)}>
        Connect Owner
      </button>
      {open && (
        <form className="connection-popover connection-form" onSubmit={submit}>
          <p><b>Control Plane connection</b><small>Owner IdentityのBearer tokenで認証します。</small></p>
          <label>API URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
          <label>Bearer token<input type="password" required value={token} onChange={(event) => setToken(event.target.value)} placeholder="Paste token" autoComplete="off" /></label>
          {error && <p className="connection-error">{error}</p>}
          <button className="primary" type="submit" disabled={loading}>{loading ? "Connecting…" : "Connect"}</button>
        </form>
      )}
    </div>
  );
}
