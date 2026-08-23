import type { Metadata } from "next";
import Link from "next/link";
import "./styles.css";

export const metadata: Metadata = {
  title: "R-CAO Control Plane",
  description: "Phase 1 off-chain organization simulator",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>
        <header className="topbar">
          <Link className="brand" href="/">R-CAO</Link>
          <nav><Link href="/">Dashboard</Link><Link href="/tasks">Task Board</Link><Link href="/operations">Operations</Link></nav>
          <span className="mode">VIRTUAL SOL · OFF-CHAIN</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
