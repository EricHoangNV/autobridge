type Props = { outcome: string | null | undefined };

const colorFor = (o: string): string => {
  if (o === "Complete") return "bg-emerald-100 text-emerald-800 border-emerald-200";
  if (o === "Degraded") return "bg-amber-100 text-amber-800 border-amber-200";
  if (o === "Blocked") return "bg-red-100 text-red-800 border-red-200";
  return "bg-zinc-100 text-zinc-600 border-zinc-200";
};

export function SyncOutcomeBadge({ outcome }: Props) {
  const label = outcome ?? "—";
  return (
    <span
      data-testid="sync-outcome-badge"
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${colorFor(label)}`}
    >
      {label}
    </span>
  );
}
