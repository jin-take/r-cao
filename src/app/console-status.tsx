"use client";

import { useMvp } from "@/app/mvp-context";

export function ConsoleStatus() {
  const {
    connected,
    loading,
    error,
    commandError,
    refresh,
    clearCommandError,
  } = useMvp();

  if (loading) {
    return <div className="console-banner info"><b>Loading Control Plane read model…</b><span>認証済みAPIからOwner Consoleのデータを取得しています。</span></div>;
  }
  if (!connected) {
    return <div className="console-banner warning"><b>Owner Console is disconnected</b><span>右上のConnect Ownerから、Python Control PlaneのOwner tokenを設定してください。ローカルseedデータは表示しません。</span></div>;
  }
  if (error) {
    return <div className="console-banner danger"><b>Control Plane read failed</b><span>{error}</span><button className="quiet-button" type="button" onClick={() => void refresh()}>Retry</button></div>;
  }
  if (commandError) {
    return <div className="console-banner danger"><b>Owner command was not completed</b><span>{commandError}</span><button className="quiet-button" type="button" onClick={() => void refresh()}>Refresh read model</button><button className="quiet-button" type="button" onClick={clearCommandError}>Dismiss</button></div>;
  }
  return null;
}
