"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { StatusPill } from "@/components/ui/status-pill";
import { createSessionAction } from "@/features/exam-sessions/actions";
import type {
  AvailableAgent,
  PolicyProfile,
} from "@/features/exam-sessions/types";

type CreateSessionFormProps = {
  agents: AvailableAgent[];
  profiles: PolicyProfile[];
};

function SubmitButton() {
  const { pending } = useFormStatus();

  return (
    <button className="session-form__submit" type="submit" disabled={pending}>
      {pending ? "Creating session..." : "Create session"}
    </button>
  );
}

export function CreateSessionForm({ agents, profiles }: CreateSessionFormProps) {
  const [state, formAction] = useActionState(createSessionAction, { error: null });

  return (
    <form action={formAction} className="session-form">
      <div className="session-form__field">
        <label htmlFor="session-name">Exam name</label>
        <input id="session-name" name="name" type="text" required />
      </div>

      <div className="session-form__field">
        <label htmlFor="session-room">Room</label>
        <input id="session-room" name="room" type="text" required />
      </div>

      <fieldset className="session-form__profiles">
        <legend>Exam policy</legend>
        <p>Select the policy that the Control Server will push after creation.</p>
        <div className="session-form__profile-list">
          {profiles.map((profile, index) => (
            <label className="session-form__profile" key={profile.id}>
              <span className="session-form__profile-heading">
                <input
                  type="radio"
                  name="policy_profile"
                  value={profile.id}
                  defaultChecked={index === 0}
                  required
                />
                <strong>{profile.label}</strong>
              </span>
              <span>{profile.description}</span>
              <pre aria-label={`${profile.label} YAML policy`}>{profile.yaml}</pre>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="session-form__agents">
        <legend>Workstation Agents</legend>
        <p>Select at least one online Agent for this exam session.</p>

        {agents.length === 0 ? (
          <p className="session-form__empty">No workstation Agents are registered.</p>
        ) : (
          <div className="session-form__agent-list">
            {agents.map((agent) => {
              const isOnline = agent.status === "ONLINE";

              return (
                <label
                  className={`session-form__agent${
                    isOnline ? "" : " session-form__agent--disabled"
                  }`}
                  key={agent.id}
                >
                  <input
                    type="checkbox"
                    name="agent_ids"
                    value={agent.id}
                    disabled={agent.status !== "ONLINE"}
                  />
                  <span className="session-form__agent-copy">
                    <strong>{agent.hostname}</strong>
                    <span>{agent.ip_address}</span>
                  </span>
                  <StatusPill
                    label={agent.status}
                    tone={isOnline ? "success" : "warning"}
                  />
                </label>
              );
            })}
          </div>
        )}
      </fieldset>

      <p className="session-form__error" role="status" aria-live="polite">
        {state.error}
      </p>

      <SubmitButton />
    </form>
  );
}
