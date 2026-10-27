type Props = { outcome: string | null | undefined };

// Square-shape status (broadcast): Complete = ink word + filled square,
// Degraded = live word + half square, Blocked = stop word + hollow square.
function shapeFor(o: string): string {
  if (o === "Complete") return "st-ok";
  if (o === "Degraded") return "st-deg";
  if (o === "Blocked") return "st-blk";
  return "st-mut";
}

export function SyncOutcomeBadge({ outcome }: Props) {
  const label = outcome ?? "—";
  return (
    <span data-testid="sync-outcome-badge" className={`st ${shapeFor(label)}`}>
      {label}
    </span>
  );
}
