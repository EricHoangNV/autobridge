type Props = {
  values: number[];
  width?: number;
  height?: number;
  label?: string;
};

/**
 * Broadcast token bars: one bar per sync/repair, zero values drawn as a
 * 2px baseline tick in --off. Nonzero bars fill in --ink. Bars are scaled
 * with headroom (REQ-048 item 4) so a lone tall value never fills the
 * whole cell height and reads as a full bar.
 */
export function Sparkline({ values, width = 200, height = 40, label }: Props) {
  if (!values.length) {
    return (
      <span data-testid="sparkline" className="text-sm text-ink-2">
        {label ?? "no data"}
      </span>
    );
  }
  const max = Math.max(...values, 1);
  const n = values.length;
  const slot = width / n;
  const barW = Math.max(2, Math.min(8, slot - 2));
  return (
    <svg
      data-testid="sparkline"
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="block"
      role="img"
      aria-label={label ?? `sparkline with ${n} points`}
    >
      {values.map((v, i) => {
        if (v <= 0) {
          const x = i * slot + (slot - barW) / 2;
          return <rect key={i} data-testid="sparkline-tick" fill="var(--off)" x={x} y={height - 2} width={barW} height={2} />;
        }
        const h = Math.max(3, (v / max) * (height - 8));
        const x = i * slot + (slot - barW) / 2;
        return <rect key={i} data-testid="sparkline-bar" fill="var(--ink)" x={x} y={height - h} width={barW} height={h} />;
      })}
    </svg>
  );
}
