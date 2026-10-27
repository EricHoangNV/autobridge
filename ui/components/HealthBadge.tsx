type Props = { status: string };

// Square-shape status (broadcast): healthy/ok = ink filled square,
// degraded/warning = live half square, blocked/error = stop hollow square.
// Never hue alone: the shape always differs too.
function shapeFor(s: string): string {
  const v = s.toLowerCase();
  if (v === "healthy" || v === "ok" || v === "active") return "st-ok";
  if (v === "degraded" || v === "warning") return "st-deg";
  if (v === "blocked" || v === "error" || v === "failed") return "st-blk";
  return "st-mut";
}

export function HealthBadge({ status }: Props) {
  return (
    <span data-testid="health-badge" className={`st ${shapeFor(status)}`}>
      {status}
    </span>
  );
}
