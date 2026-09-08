type StatusPillProps = {
  label: string;
  tone: "neutral" | "success" | "warning";
};

export function StatusPill({ label, tone }: StatusPillProps) {
  return <span className={`status-pill status-pill--${tone}`}>{label}</span>;
}

