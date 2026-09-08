import Link from "next/link";
import { CreateSessionForm } from "@/features/exam-sessions";
import {
  getAvailableAgents,
  getPolicyProfiles,
} from "@/features/exam-sessions/queries";

export const dynamic = "force-dynamic";

export default async function CreateSessionPage() {
  const [agents, profiles] = await Promise.all([
    getAvailableAgents(),
    getPolicyProfiles(),
  ]);

  return (
    <main className="page-shell">
      <header className="hero sessions-create-hero">
        <Link className="sessions-back-link" href="/sessions">
          &larr; Back to sessions
        </Link>
        <p className="eyebrow">Session setup</p>
        <h1>Create Exam Session</h1>
        <p>Choose the room and online workstation Agents assigned to this exam.</p>
      </header>

      {agents === null || profiles === null ? (
        <section className="sessions-card" aria-labelledby="sessions-api-title">
          <h2 id="sessions-api-title">API unavailable</h2>
          <p className="sessions-message">
            Cannot load workstation Agents from the control server. Try again shortly.
          </p>
        </section>
      ) : profiles.length === 0 ? (
        <section className="sessions-card">
          <p className="sessions-message">No policy profiles are configured.</p>
        </section>
      ) : (
        <CreateSessionForm agents={agents} profiles={profiles} />
      )}
    </main>
  );
}
