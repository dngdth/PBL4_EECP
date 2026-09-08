import Link from "next/link";
import { SessionList } from "@/features/exam-sessions";

export const dynamic = "force-dynamic";

export default function SessionsPage() {
  return (
    <main className="page-shell">
      <header className="hero sessions-hero">
        <div>
          <p className="eyebrow">Control Server</p>
          <h1>Exam Sessions</h1>
          <p>Create, prepare, run, and finish managed exam sessions.</p>
        </div>
        <div className="sessions-hero__actions">
          <Link className="sessions-secondary-link" href="/policies">
            Manage policies
          </Link>
          <Link className="sessions-primary-link" href="/sessions/create">
            Create session
          </Link>
        </div>
      </header>
      <SessionList />
    </main>
  );
}
