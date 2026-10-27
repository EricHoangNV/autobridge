type Props = {
  values: number[];
  width?: number;
  height?: number;
  label?: string;
};

export function Sparkline({ values, width = 80, height = 24, label }: Props) {
  if (!values.length) {
    return (
      <span data-testid="sparkline" className="text-xs text-ink-2">
        {label ?? "no data"}
      </span>
    );
  }
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = max - min || 1;
  const stepX = values.length > 1 ? width / (values.length - 1) : width;
  const points = values
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      data-testid="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="inline-block overflow-visible"
      role="img"
      aria-label={label ?? `sparkline with ${values.length} points`}
    >
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        className="text-ink-2"
        points={points}
      />
    </svg>
  );
}
