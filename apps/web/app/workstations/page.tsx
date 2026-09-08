import { WorkstationList } from "@/features/workstations";

export const dynamic = "force-dynamic";

export default function WorkstationsPage() {
  return (
    <main className="page-shell">
      <header className="hero">
        <p className="eyebrow">Control Server</p>
        <h1>Exam Workstations</h1>
        <p>Live registration and heartbeat status for workstation agents.</p>
      </header>
      <WorkstationList />
    </main>
  );
}
