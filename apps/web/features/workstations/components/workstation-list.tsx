import { StatusPill } from "@/components/ui/status-pill";
import { AutoRefresh } from "@/components/ui/auto-refresh";
import { getAgents } from "@/features/workstations/queries";

export async function WorkstationList() {
  const agents = await getAgents();

  return (
    <section className="workstations-card" aria-labelledby="workstations-title">
      <AutoRefresh />
      <div className="workstations-card__header">
        <div>
          <p className="eyebrow">Live status</p>
          <h2 id="workstations-title">Workstations</h2>
        </div>
        <StatusPill
          label={agents === null ? "API unavailable" : `${agents.length} registered`}
          tone={agents === null ? "warning" : "neutral"}
        />
      </div>

      {agents === null ? (
        <p className="workstations-message">
          Cannot reach the control server. The dashboard will retry automatically.
        </p>
      ) : agents.length === 0 ? (
        <p className="workstations-message">
          No workstation agents have registered yet.
        </p>
      ) : (
        <div className="workstations-grid">
          {agents.map((agent) => (
            <article className="workstation" key={agent.id}>
              <div className="workstation__heading">
                <div>
                  <p className="workstation__id">{agent.id}</p>
                  <h3>{agent.hostname}</h3>
                </div>
                <StatusPill
                  label={agent.status}
                  tone={agent.status === "ONLINE" ? "success" : "warning"}
                />
              </div>
              <dl className="workstation__details">
                <div>
                  <dt>IP address</dt>
                  <dd>{agent.ip_address}</dd>
                </div>
                <div>
                  <dt>Agent version</dt>
                  <dd>{agent.agent_version}</dd>
                </div>
                <div>
                  <dt>Last heartbeat</dt>
                  <dd>
                    <time dateTime={agent.last_seen}>
                      {new Intl.DateTimeFormat("en-GB", {
                        dateStyle: "medium",
                        timeStyle: "medium",
                      }).format(new Date(agent.last_seen))}
                    </time>
                  </dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
