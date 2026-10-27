"use client";

type Props = {
  verifierReportId?: string | null;
  visibleTestResults?: Array<Record<string, unknown>> | null;
  escalationBundle?: Record<string, unknown> | null;
  decision: string;
};

export function VerifierVerdict({ verifierReportId, visibleTestResults, escalationBundle, decision }: Props) {
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

  return (
    <div data-testid="verifier-verdict" className="overflow-hidden border bg-band">
      <div className="border-b bg-ground px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">Verifier verdict</h3>
      </div>

      <div className="px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span
            data-testid="verifier-verdict-badge"
            data-verdict={verdict}
            className={`inline-flex items-center border px-2.5 py-0.5 text-xs font-medium ${
              verdict === "pass"
                ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                : verdict === "fail"
                  ? "bg-band text-stop border-stop"
                  : verdict === "pending"
                    ? "bg-ground text-ink-2 border-rule"
                    : "bg-amber-100 text-amber-800 border-amber-200"
            }`}
          >
            {verdict}
          </span>
          {hasReport ? (
            <span data-testid="verifier-report-id" className="font-mono text-xs text-ink-2">
              report {verifierReportId!.slice(0, 8)}
            </span>
          ) : null}
          {!hasReport && hasEscalation ? (
            <span className="text-xs text-amber-700">no verifier report — escalated</span>
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
          <div
            data-testid="escalation-bundle"
            className="mt-3 border border-amber-200 bg-amber-50 px-3 py-2.5"
          >
            <div className="text-xs font-medium text-amber-900">Escalation bundle</div>
            {escalationBundle.reason ? (
              <div data-testid="escalation-reason" className="mt-1 text-xs text-amber-800">
                Reason: {String(escalationBundle.reason)}
              </div>
            ) : null}
            {escalationBundle.question ? (
              <div data-testid="escalation-question" className="mt-1 text-xs font-medium text-amber-900">
                Q: {String(escalationBundle.question)}
              </div>
            ) : null}
            {escalationBundle.suggested_action ? (
              <div className="mt-1 text-xs text-amber-700">Action: {String(escalationBundle.suggested_action)}</div>
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
    </div>
  );
}
