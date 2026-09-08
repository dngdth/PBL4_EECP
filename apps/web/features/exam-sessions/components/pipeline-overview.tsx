import { StatusPill } from "@/components/ui/status-pill";
import { getApiHealth } from "@/features/exam-sessions/queries";
import type { PipelineStage } from "@/features/exam-sessions/types";

const stages: PipelineStage[] = [
  { state: "CREATED", label: "Create", description: "Tạo ca thi và danh sách máy." },
  { state: "DEPLOYING", label: "Deploy", description: "Phát policy tới Agent và Gateway." },
  { state: "PREFLIGHT", label: "Preflight", description: "Đối chiếu desired/actual state." },
  { state: "READY", label: "Ready", description: "Áp dụng readiness gate trước khi thi." },
  { state: "RUNNING", label: "Monitor", description: "Nhận telemetry và gom incident." },
  { state: "RESTORING", label: "Restore", description: "Khôi phục baseline sau ca thi." },
  { state: "NORMAL", label: "Complete", description: "Xác nhận máy trở về bình thường." },
];

export async function PipelineOverview() {
  const health = await getApiHealth();

  return (
    <section className="pipeline-card" aria-labelledby="pipeline-title">
      <div className="pipeline-card__header">
        <div>
          <p className="eyebrow">Control Server</p>
          <h2 id="pipeline-title">Exam session pipeline</h2>
        </div>
        <StatusPill
          label={health ? "API connected" : "API unavailable"}
          tone={health ? "success" : "warning"}
        />
      </div>

      <ol className="pipeline">
        {stages.map((stage, index) => (
          <li className="pipeline__stage" key={stage.state}>
            <span className="pipeline__index">{String(index + 1).padStart(2, "0")}</span>
            <div>
              <strong>{stage.label}</strong>
              <code>{stage.state}</code>
              <p>{stage.description}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

