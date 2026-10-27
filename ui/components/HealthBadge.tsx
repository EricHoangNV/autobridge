type Props = { status: string };

const colorFor = (s: string): string => {
  const v = s.toLowerCase();
  if (v === "healthy" || v === "ok" || v === "active") return "bg-emerald-100 text-emerald-800 border-emerald-200";
  if (v === "degraded" || v === "warning") return "bg-amber-100 text-amber-800 border-amber-200";
  if (v === "blocked" || v === "error" || v === "failed") return "bg-band text-stop border-stop";
  if (v === "paused" || v === "healing") return "bg-blue-100 text-blue-800 border-blue-200";
  return "bg-ground text-ink-2 border-rule";
};

export function HealthBadge({ status }: Props) {
  return (
    <span
      data-testid="health-badge"
      className={`inline-flex items-center border px-2 py-0.5 text-xs font-medium ${colorFor(status)}`}
    >
      {status}
    </span>
  );
}
