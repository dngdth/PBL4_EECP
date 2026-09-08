import { PipelineOverview } from "@/features/exam-sessions";

export const dynamic = "force-dynamic";

export default function HomePage() {
  return (
    <main className="page-shell">
      <header className="hero">
        <p className="eyebrow">Exam Environment Control Platform</p>
        <h1>Phòng thi sẵn sàng, policy có thể kiểm chứng.</h1>
        <p>
          Dashboard điều phối policy, preflight, giám sát sự cố và khôi phục môi trường thi.
        </p>
      </header>
      <PipelineOverview />
    </main>
  );
}

