"use client";

const LEVEL_LABELS: Record<number, string> = {
  0: "L0 Knowledge",
  1: "L1 Rules",
  2: "L2 Small model",
  3: "L3 Frontier",
  4: "L4 Human",
};

const LEVEL_COLORS: Record<number, string> = {
  0: "bg-sky-100 text-sky-800 border-sky-200",
  1: "bg-emerald-100 text-emerald-800 border-emerald-200",
  2: "bg-violet-100 text-violet-800 border-violet-200",
  3: "bg-amber-100 text-amber-800 border-amber-200",
  4: "bg-band text-stop border-stop",
};

function levelBadge(level: number) {
  const label = LEVEL_LABELS[level] ?? `L${level}`;
  const color = LEVEL_COLORS[level] ?? "bg-ground text-ink-2 border-rule";
  return { label, color };
}

type LadderStep = {
  level?: number;
  action?: string;
  reason?: string;
  rule_id?: string;
  drift_class?: string;
  patch?: unknown;
  confidence?: number;
  [key: string]: unknown;
};

type Props = {
  ladderLevelReached: number | null | undefined;
  ruleId?: string | null;
  decision: string;
  escalationBundle?: Record<string, unknown> | null;
  trace?: LadderStep[] | null;
};

export function LadderTrace({ ladderLevelReached, ruleId, decision, escalationBundle, trace }: Props) {
  const level = ladderLevelReached ?? 0;
  const badge = levelBadge(level);

  // Derive steps: if trace array provided use it, otherwise synthesize from repair fields
  const steps: LadderStep[] = (() => {
    if (trace && trace.length > 0) return trace;
    // Synthesize single-step view from repair columns
    const s: LadderStep[] = [];
    if (level === 0 && ruleId) {
      s.push({ level: 0, action: "knowledge_hit", reason: `L0 cache hit — rule ${ruleId}`, rule_id: ruleId });
    } else if (level === 1 && ruleId) {
      s.push({ level: 1, action: "propose", reason: `L1 rule ${ruleId} matched`, rule_id: ruleId });
    } else if (level >= 2) {
      s.push({ level, action: decision === "escalate" ? "escalate" : "propose", reason: `Reached L${level} — ${decision}` });
    } else if (decision === "escalate" && escalationBundle) {
      s.push({ level: 4, action: "escalate", reason: String(escalationBundle.reason ?? "Escalated to human") });
    } else {
      s.push({ level, action: decision, reason: `Decision: ${decision} at L${level}` });
    }
    return s;
  })();

  return (
    <div data-testid="ladder-trace" className="border bg-band">
      {/* Header badge */}
      <div className="flex items-center justify-between border-b bg-ground px-4 py-3">
        <h3 className="text-sm font-semibold">Ladder trace</h3>
        <span
          data-testid="ladder-level-badge"
          data-level={String(level)}
          className={`inline-flex items-center border px-2.5 py-0.5 text-xs font-medium ${badge.color}`}
        >
          {badge.label}
        </span>
      </div>

      {/* Decision + rule summary */}
      <div className="border-b bg-band px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-ink-2">Decision:</span>
          <span
            data-testid="ladder-decision"
            className={`inline-flex border px-2 py-0.5 text-xs font-medium ${
              decision === "propose"
                ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                : decision === "escalate"
                  ? "bg-band text-stop border-stop"
                  : decision === "reject"
                    ? "bg-ground text-ink-2 border-rule"
                    : "bg-amber-50 text-amber-800 border-amber-200"
            }`}
          >
            {decision}
          </span>
          {ruleId ? (
            <span data-testid="ladder-rule-id" className="font-mono text-xs text-ink-2">
              rule: {ruleId}
            </span>
          ) : null}
          {escalationBundle?.question ? (
            <span data-testid="ladder-escalation-question" className="text-xs text-amber-800">
              Q: {String(escalationBundle.question)}
            </span>
          ) : null}
        </div>
      </div>

      {/* Step list */}
      <ol className="divide-y divide-rule">
        {steps.map((step, idx) => {
          const lvl = typeof step.level === "number" ? step.level : level;
          const b = levelBadge(lvl);
          return (
            <li
              key={idx}
              data-testid={`ladder-step-${idx}`}
              data-level={String(lvl)}
              className="flex items-start gap-3 px-4 py-3"
            >
              <span
                data-testid={`ladder-step-badge-${idx}`}
                className={`mt-0.5 inline-flex shrink-0 items-center border px-2 py-0.5 text-[11px] font-medium ${b.color}`}
              >
                {b.label}
              </span>
              <div className="min-w-0 flex-1">
                {step.action ? (
                  <div data-testid={`ladder-step-action-${idx}`} className="text-xs font-medium text-ink">
                    {step.action}
                  </div>
                ) : null}
                {step.reason ? (
                  <div data-testid={`ladder-step-reason-${idx}`} className="text-xs text-ink-2">
                    {step.reason}
                  </div>
                ) : null}
                {step.rule_id && step.rule_id !== ruleId ? (
                  <div className="font-mono text-[11px] text-ink-2">rule: {String(step.rule_id)}</div>
                ) : null}
                {step.drift_class ? (
                  <div className="text-[11px] text-ink-2">drift: {String(step.drift_class)}</div>
                ) : null}
              </div>
              {/* Level number marker */}
              <span className="shrink-0 font-mono text-[11px] text-ink-2">L{lvl}</span>
            </li>
          );
        })}
      </ol>

      {/* All level badges legend (for visual completeness per spec: level badges) */}
      <div className="flex flex-wrap gap-1.5 border-t bg-ground px-4 py-2">
        {[0, 1, 2, 3, 4].map((l) => {
          const b = levelBadge(l);
          const isReached = l === level;
          return (
            <span
              key={l}
              data-testid={`ladder-legend-L${l}`}
              data-reached={isReached ? "true" : "false"}
              className={`inline-flex items-center border px-2 py-0.5 text-[11px] font-medium ${b.color} ${isReached ? "ring-1 ring-live" : "opacity-60"}`}
            >
              {b.label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
