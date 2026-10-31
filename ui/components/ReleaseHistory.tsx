"use client";

import type { ReleaseOut } from "@/lib/api/client";

type Props = {
  releases: ReleaseOut[];
  activeReleaseId?: string | null;
  onActivate?: (releaseId: string) => void;
  activatingId?: string | null;
};

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return `today ${time}`;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return `yesterday ${time}`;
    return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
  } catch {
    return iso;
  }
}

export function ReleaseHistory({ releases, activeReleaseId, onActivate, activatingId }: Props) {
  if (!releases.length) {
    return (
      <div data-testid="release-history-empty" className="border border-rule px-4 py-6 text-sm text-ink-2">
        <span data-testid="no-releases">No releases yet. Trigger a build to create the first release.</span>
      </div>
    );
  }

  // Show newest first
  const sorted = [...releases].sort((a, b) => b.version - a.version);

  return (
    <div data-testid="release-history">
      <p className="rels-head">
        <span>Release</span>
        <span>Approved by</span>
        <span
          tabIndex={0}
          title="The release's HMAC-SHA256 spec signature — proves this release's spec is exactly the one that was verified and approved."
        >
          Signature
        </span>
      </p>
      <ol data-testid="release-history-table" className="rels num">
        {sorted.map((r) => {
          const isActive = r.id === activeReleaseId;
          const isCandidate = !isActive && r === sorted[0];
          return (
            <li key={r.id} data-testid={`release-row-${r.id}`} className={isCandidate ? "cand" : undefined}>
              <span className="id" tabIndex={0} title={`Release ${r.id}`}>
                r{r.version}
              </span>
              <span className="t">
                {isActive ? "Live" : isCandidate ? "Candidate" : `v${r.version}`} · {formatDate(r.created_at)}
              </span>
              <span
                className="k"
                tabIndex={0}
                title={`${r.signature} — the release's HMAC-SHA256 spec signature: proves this release's spec is exactly the one that was verified and approved.`}
              >
                {r.signature.slice(0, 12)}…
              </span>
              <span className="s">
                {r.approved_by ? (
                  <span data-testid={`release-approved-by-${r.id}`}>approved by {r.approved_by}</span>
                ) : isCandidate ? (
                  <span className="text-ink-2">needs your approval</span>
                ) : r.provenance_build_run_id && r.verification_report_id ? (
                  <span
                    data-testid={`release-approved-by-${r.id}`}
                    title="This release went live automatically: the verifier passed it at build time, so no operator approval was needed. Every later change waits for your approval."
                  >
                    automatic · verifier passed
                  </span>
                ) : (
                  <span className="text-ink-2">—</span>
                )}
                {isActive ? (
                  <>
                    {" "}
                    <span data-testid={`release-active-${r.id}`}>active</span>
                  </>
                ) : !r.approved_by && !r.provenance_repair_run_id && onActivate ? (
                  <>
                    {" "}
                    <button
                      type="button"
                      data-testid={`release-activate-${r.id}`}
                      disabled={activatingId === r.id}
                      onClick={() => onActivate(r.id)}
                      className="btn quiet"
                    >
                      {activatingId === r.id ? "Activating…" : "Activate"}
                    </button>
                  </>
                ) : null}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
