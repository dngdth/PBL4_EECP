import { StatusPill } from "@/components/ui/status-pill";
import { DeletePolicyForm } from "@/features/policy-profiles/components/delete-policy-form";
import { PolicyProfileForm } from "@/features/policy-profiles/components/policy-profile-form";
import type { PolicyProfile } from "@/features/policy-profiles/types";

export function PolicyProfileManager({ profiles }: { profiles: PolicyProfile[] }) {
  return (
    <div className="policy-manager">
      <section className="policy-create-card">
        <p className="eyebrow">New profile</p>
        <h2>Create policy profile</h2>
        <PolicyProfileForm />
      </section>

      <section className="policy-list" aria-labelledby="policy-list-title">
        <div className="policy-list__heading">
          <div>
            <p className="eyebrow">Catalog</p>
            <h2 id="policy-list-title">Available profiles</h2>
          </div>
          <StatusPill label={`${profiles.length} total`} tone="neutral" />
        </div>
        {profiles.map((profile) => (
          <article className="policy-card" key={profile.id}>
            <div className="policy-card__heading">
              <div>
                <code>{profile.id}</code>
                <h3>{profile.label}</h3>
                <p>{profile.description}</p>
              </div>
              <StatusPill
                label={profile.is_builtin ? "BUILT-IN" : "CUSTOM"}
                tone={profile.is_builtin ? "neutral" : "success"}
              />
            </div>
            <details>
              <summary>YAML preview</summary>
              <pre>{profile.yaml}</pre>
            </details>
            {profile.is_builtin ? (
              <p className="policy-card__readonly">
                Built-in profiles are read-only and remain available for recovery.
              </p>
            ) : (
              <>
                <PolicyProfileForm profile={profile} />
                <DeletePolicyForm profileId={profile.id} />
              </>
            )}
          </article>
        ))}
      </section>
    </div>
  );
}
