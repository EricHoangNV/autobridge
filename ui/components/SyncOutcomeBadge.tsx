type Props = { outcome: string | null | undefined };

const colorFor = (o: string): string => {
  if (o === "Complete") return "bg-emerald-100 text-emerald-800 border-emerald-200";
  if (o === "Degraded") return "bg-amber-100 text-amber-800 border-amber-200";
  if (o === "Blocked") return "bg-band text-stop border-stop";
  return "bg-ground text-ink-2 border-rule";
};

export function SyncOutcomeBadge({ outcome }: Props) {
  const label = outcome ?? "—";
  return (
    <span
      data-testid="sync-outcome-badge"
      className={`inline-flex items-center border px-2 py-0.5 text-xs font-medium ${colorFor(label)}`}
    >
      {label}
    </span>
  );
}
