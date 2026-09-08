import Link from "next/link";
import { PolicyProfileManager } from "@/features/policy-profiles";
import { getPolicyProfiles } from "@/features/policy-profiles/queries";

export const dynamic = "force-dynamic";

export default async function PoliciesPage() {
  const profiles = await getPolicyProfiles();
  return (
    <main className="page-shell">
      <header className="hero sessions-hero">
        <div>
          <Link className="sessions-back-link" href="/sessions">
            &larr; Back to sessions
          </Link>
          <p className="eyebrow">Phase 3.2</p>
          <h1>Policy Profiles</h1>
          <p>Create reusable exam controls and manage teacher-defined profiles.</p>
        </div>
      </header>
      {profiles === null ? (
        <p className="sessions-message">Cannot load policy profiles from the Control Server.</p>
      ) : (
        <PolicyProfileManager profiles={profiles} />
      )}
    </main>
  );
}
