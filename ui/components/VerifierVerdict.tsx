"use client";

type Props = {
  verifierReportId?: string | null;
  visibleTestResults?: Array<Record<string, unknown>> | null;
  escalationBundle?: Record<string, unknown> | null;
  decision: string;
  // REQ-047 item 3: when the model escalated, no patch was proposed, so no
  // check ran — say so instead of showing a fail verdict.
  modelEscalated?: boolean;
  // REQ-055 item 3c: a tested proposal (dry-run passed) rides the
  // escalation — show the dry-run result here instead of "no patch to
  // verify", which contradicts the proposal beside it.
  proposalDryRun?: string | null;
};

function countPassed(results: Array<Record<string, unknown>>): { passed: number; total: number } {
  let passed = 0;
  for (const t of results) {
    const v = t as Record<string, unknown>;
    if (v["passed"] === true || v["ok"] === true) {
      passed += 1;
      continue;
    }
    const s = typeof v["status"] === "string" ? (v["status"] as string).toLowerCase() : "";
    const r = typeof v["result"] === "string" ? (v["result"] as string).toLowerCase() : "";
    if (["pass", "passed", "ok", "success"].includes(s) || ["pass", "passed"].includes(r)) passed += 1;
  }
  return { passed, total: results.length };
}

/**
 * Display form of an escalation bundle reason (REQ-048 item 3): the
 * escalation question and the model's rationale live once in the top
 * decision box, so the bundle box shows the reason and the suggested
 * action without repeating them. Reasons without the backend's trailing
 * "model rationale: …" clause pass through unchanged.
 */
export function displayBundleReason(reason: unknown): string {
  const text = String(reason ?? "");
  return text.replace(/;?\s*model rationale:.*$/is, "").trim();
}

export function VerifierVerdict({ verifierReportId, visibleTestResults, escalationBundle, decision, modelEscalated = false, proposalDryRun = null }: Props) {
  const hasReport = !!verifierReportId;
  const hasEscalation = !!escalationBundle;

  // Infer verdict from report existence + decision + visible tests
  const verdict: string = (() => {
    if (hasReport && decision === "propose") return "pass";
    if (hasReport && decision === "escalate") return "fail";
    if (!hasReport && decision === "escalate") return "not_verified";
    if (!hasReport && !hasEscalation) return "pending";
    return decision === "propose" ? "pass" : "fail";
  })();

  const { passed, total } = countPassed(visibleTestResults ?? []);

  return (
    <div data-testid="verifier-verdict">
      <h2 className="kicker">Verifier, hidden fixtures</h2>
      {verdict === "pending" ? (
        <p className="pend-only" style={{ display: "block", fontSize: 26, lineHeight: 1.2, fontWeight: 750, color: "var(--ink-2)", maxWidth: "18ch" }}>
          Not run yet. The verifier waits for a patch.
        </p>
      ) : null}
      <dl className="repair-score repair-past">
        <dt className="num">
          {total > 0 ? passed : "—"}
          <small>/{total > 0 ? total : "—"}</small>
        </dt>
        <dd>checks pass{hasReport ? " on this patch" : " so far"}</dd>
      </dl>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span data-testid="verifier-verdict-badge" data-verdict={verdict} className="font-mono text-xs text-ink-2">
          {verdict}
        </span>
        {hasReport ? (
          <span data-testid="verifier-report-id" className="font-mono text-xs text-ink-2">
            report {verifierReportId!.slice(0, 8)}
          </span>
        ) : null}
        {!hasReport && hasEscalation ? (
          <span className="text-xs text-ink-2" data-testid="verifier-dry-run-note">
            {proposalDryRun ? `tested proposal — ${proposalDryRun}` : modelEscalated ? "no patch to verify (the model escalated)" : "no verifier report — escalated"}
          </span>
        ) : null}
      </div>

      {verifierReportId ? (
        <div className="mt-2 text-xs text-ink-2">
          Verifier report{" "}
          <span className="font-mono text-ink" title={verifierReportId}>
            {verifierReportId.slice(0, 12)}…
          </span>{" "}
          linked to this repair. Full fixture rows are never returned in API responses.
        </div>
      ) : null}

      {escalationBundle ? (
        <div data-testid="escalation-bundle" className="mt-3 border border-rule px-3 py-2.5">
          <div className="text-xs font-medium text-ink">Escalation bundle</div>
          {escalationBundle.reason ? (
            <div data-testid="escalation-reason" className="mt-1 text-xs text-ink-2">
              Reason: {displayBundleReason(escalationBundle.reason)}
            </div>
          ) : null}
          {/* REQ-048 item 3: the question (and rationale) live once in the
              top decision box — the bundle keeps reason/action only. */}
          {escalationBundle.suggested_action ? (
            <div className="mt-1 text-xs text-ink-2">Action: {String(escalationBundle.suggested_action)}</div>
          ) : null}
          {Array.isArray(escalationBundle.evidence_ids) && escalationBundle.evidence_ids.length ? (
            <div className="mt-1 text-xs text-ink-2">
              Evidence: {(escalationBundle.evidence_ids as string[]).map((eid) => eid.slice(0, 8)).join(", ")}
            </div>
          ) : null}
        </div>
      ) : null}

      {visibleTestResults?.length ? (
        <div className="mt-3 text-xs text-ink-2">
          {visibleTestResults.length} visible test(s) — see “Visible tests” below.
        </div>
      ) : null}
    </div>
  );
}
