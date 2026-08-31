import type { Metadata } from "next";
import Link from "next/link";
import { ConnectionPanel } from "@/app/connection-panel";
import { ConsoleStatus } from "@/app/console-status";
import { MvpProvider } from "@/app/mvp-context";
import "./styles.css";

export const metadata: Metadata = {
  title: "R-CAO Control Plane",
  description: "Phase 1 off-chain organization simulator",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>
        <MvpProvider>
          <header className="topbar">
            <Link className="brand" href="/">R-CAO</Link>
            <nav>
              <Link href="/dashboard">Dashboard</Link>
              <Link href="/tasks">Tasks</Link>
              <Link href="/agents">Agents</Link>
              <Link href="/approvals">Approvals</Link>
              <Link href="/rewards">Rewards</Link>
              <Link href="/proposals">Proposals</Link>
              <Link href="/audit">Audit</Link>
              <Link href="/operations">Operations</Link>
            </nav>
            <span className="mode">VIRTUAL SOL · OFF-CHAIN</span>
            <ConnectionPanel />
          </header>
          <ConsoleStatus />
          <main>{children}</main>
        </MvpProvider>
      </body>
    </html>
  );
}
