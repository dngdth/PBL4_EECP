import { StatusPill } from "@/components/ui/status-pill";
import { AutoRefresh } from "@/components/ui/auto-refresh";
import { getSessions } from "@/features/exam-sessions/queries";
import type { SessionStatus } from "@/features/exam-sessions/types";
import { TransitionForm } from "./transition-form";

const nextStatus = {
  CREATED: ["READY", "Mark Ready"],
  READY: ["RUNNING", "Start Exam"],
  RUNNING: ["FINISHED", "Finish Exam"],
} as const;

function statusTone(status: SessionStatus) {
  if (status === "READY" || status === "RUNNING" || status === "FINISHED") {
    return "success" as const;
  }

  if (status === "DEGRADED") {
    return "warning" as const;
  }

  return "neutral" as const;
}

function policyTone(status: string) {
  if (status === "APPLIED" || status === "RESTORED") return "success" as const;
  if (status === "FAILED") return "warning" as const;
  return "neutral" as const;
}

export async function SessionList() {
  const sessions = await getSessions();

  return (
    <section className="sessions-card" aria-labelledby="sessions-title">
      <AutoRefresh />
      <div className="sessions-card__header">
        <div>
          <p className="eyebrow">Live sessions</p>
          <h2 id="sessions-title">Exam sessions</h2>
        </div>
        <StatusPill
          label={sessions === null ? "API unavailable" : `${sessions.length} total`}
          tone={sessions === null ? "warning" : "neutral"}
        />
      </div>

      {sessions === null ? (
        <p className="sessions-message">
          Cannot reach the control server. The dashboard will retry automatically.
        </p>
      ) : sessions.length === 0 ? (
        <p className="sessions-message">No exam sessions have been created yet.</p>
      ) : (
        <div className="sessions-grid">
          {sessions.map((session) => {
            const transition = nextStatus[session.status as keyof typeof nextStatus];
            const violations = session.violations ?? [];
            const policyApplied = session.agents.every(
              (agent) => agent.policy_status === "APPLIED",
            );
            const policyFailed = session.agents.some(
              (agent) => agent.policy_status === "FAILED",
            );
            const policyBlocksReadiness =
              session.gateway_id === null &&
              session.status === "CREATED" &&
              !policyApplied;

            return (
              <article className="session-card" key={session.id}>
                <div className="session-card__heading">
                  <div>
                    <p className="session-card__room">{session.room}</p>
                    <h3>{session.name}</h3>
                  </div>
                  <StatusPill
                    label={session.status}
                    tone={statusTone(session.status)}
                  />
                </div>

                <dl className="session-card__details">
                  <div>
                    <dt>Session ID</dt>
                    <dd>{session.id}</dd>
                  </div>
                  <div>
                    <dt>Management</dt>
                    <dd>{session.gateway_id === null ? "Direct" : "Gateway"}</dd>
                  </div>
                  <div>
                    <dt>Assigned Agents</dt>
                    <dd>{session.agent_count}</dd>
                  </div>
                  <div>
                    <dt>Policy</dt>
                    <dd>
                      {session.policy
                        ? `${session.policy.profile} · v${session.policy.version}`
                        : "Not assigned"}
                    </dd>
                  </div>
                </dl>

                <div className="session-agents">
                  <h4>Assigned Agents</h4>
                  {session.agents.length === 0 ? (
                    <p className="session-agents__empty">No Agents assigned.</p>
                  ) : (
                    <ul className="session-agents__list">
                      {session.agents.map((agent) => (
                        <li className="session-agent" key={agent.id}>
                          <div className="session-agent__identity">
                            <strong>{agent.id}</strong>
                            <span>
                              {agent.hostname ?? "Unknown host"} ·{" "}
                              {agent.ip_address ?? "IP unavailable"}
                            </span>
                          </div>
                          <div className="session-agent__statuses">
                            <StatusPill
                              label={agent.status ?? "unavailable"}
                              tone={agent.status === "ONLINE" ? "success" : "warning"}
                            />
                            <StatusPill
                              label={session.status}
                              tone={statusTone(session.status)}
                            />
                            <StatusPill
                              label={`POLICY ${agent.policy_status}`}
                              tone={policyTone(agent.policy_status)}
                            />
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {violations.length > 0 ? (
                  <section className="session-violations" aria-label="Policy violations">
                    <h4>Policy violations</h4>
                    <ul>
                      {violations.map((violation, index) => (
                        <li key={`${violation.workstation_id}-${violation.occurred_at}-${index}`}>
                          <strong>{violation.workstation_id}</strong>
                          <span>
                            Attempted to access {violation.destination ?? "a blocked website"}
                          </span>
                          <time dateTime={violation.occurred_at}>
                            {new Date(violation.occurred_at).toLocaleString("vi-VN")}
                          </time>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {policyBlocksReadiness ? (
                  <p className="session-policy-gate" role="status">
                    {policyFailed
                      ? "Policy delivery failed. Resolve the Agent before marking this session ready."
                      : "Waiting for every Agent to acknowledge the policy."}
                  </p>
                ) : null}

                {session.gateway_id === null && transition && !policyBlocksReadiness ? (
                  <TransitionForm
                    sessionId={session.id}
                    target={transition[0]}
                    label={transition[1]}
                  />
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
