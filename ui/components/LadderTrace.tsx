"use client";

const LEVEL_NAMES: Record<number, string> = {
  0: "Knowledge",
  1: "Rules",
  2: "Small model",
  3: "Frontier model",
  4: "You",
};

function levelName(level: number): string {
  return LEVEL_NAMES[level] ?? `L${level}`;
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
  /** Optional per-level token labels for the track (real data from the repair). */
  levelTokens?: Partial<Record<0 | 1 | 2 | 3 | 4, string>>;
};

export function LadderTrace({ ladderLevelReached, ruleId, decision, escalationBundle, trace, levelTokens }: Props) {
  const level = ladderLevelReached ?? 0;

  // Derive steps: if trace array provided use it, otherwise synthesize from repair fields
  const steps: LadderStep[] = (() => {
    if (trace && trace.length > 0) return trace;
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

  const tokenLabel = (l: number): string => {
    if (levelTokens && levelTokens[l as 0 | 1 | 2 | 3 | 4] !== undefined) {
      return String(levelTokens[l as 0 | 1 | 2 | 3 | 4]);
    }
    if (l === 0 || l === 1) return "0 tokens";
    if (l === 4) return decision === "escalate" ? "you" : "approve";
    return "—";
  };

  return (
    <div data-testid="ladder-trace">
      {/* Five-segment track: the ladder as broadcast segments */}
      <ol className="repair-track" aria-label="Repair ladder">
        {[0, 1, 2, 3, 4].map((l) => {
          const isNow = l === level;
          const isTodo = l > level;
          return (
            <li
              key={l}
              data-testid={`ladder-legend-L${l}`}
              data-reached={isNow ? "true" : "false"}
              aria-current={isNow ? "step" : undefined}
              className={`repair-seg ${isNow ? "now" : ""} ${isTodo ? "todo" : ""}`}
            >
              <span className="fill" aria-hidden="true" />
              <span className="lv num">L{l}</span>
              <span className="nm">{levelName(l)}</span>
              <span className="tk num">{tokenLabel(l)}</span>
            </li>
          );
        })}
      </ol>

      {/* Reached-rung badge + decision/rule summary (kept for screen readers + tests) */}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span data-testid="ladder-level-badge" data-level={String(level)} className="font-mono text-xs text-ink-2">
          L{level} {levelName(level)}
        </span>
        <span className="text-ink-2">Decision:</span>
        <span data-testid="ladder-decision" className="text-xs font-medium text-ink">
          {decision}
        </span>
        {ruleId ? (
          <span data-testid="ladder-rule-id" className="font-mono text-xs text-ink-2">
            rule: {ruleId}
          </span>
        ) : null}
        {escalationBundle?.question ? (
          <span data-testid="ladder-escalation-question" className="text-xs text-ink">
            Q: {String(escalationBundle.question)}
          </span>
        ) : null}
      </div>

      {/* Step list */}
      <ol className="mt-2 divide-y divide-rule border-t border-rule">
        {steps.map((step, idx) => {
          const lvl = typeof step.level === "number" ? step.level : level;
          return (
            <li
              key={idx}
              data-testid={`ladder-step-${idx}`}
              data-level={String(lvl)}
              className="flex items-start gap-3 py-2.5"
            >
              <span data-testid={`ladder-step-badge-${idx}`} className="mt-0.5 inline-flex shrink-0 font-mono text-[11px] text-ink-2">
                L{lvl}
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
              <span className="shrink-0 font-mono text-[11px] text-ink-2">L{lvl}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
