"use client";

import type { ReleaseOut } from "@/lib/api/client";

type Props = {
  releases: ReleaseOut[];
  activeReleaseId?: string | null;
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

export function ReleaseHistory({ releases, activeReleaseId }: Props) {
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
      </p>
      <ol data-testid="release-history-table" className="rels num">
        {sorted.map((r) => {
          const isActive = r.id === activeReleaseId;
          const isCandidate = !isActive && r === sorted[0];
          return (
            <li key={r.id} data-testid={`release-row-${r.id}`} className={isCandidate ? "cand" : undefined}>
              <span className="id">r{r.version}</span>
              <span className="t">
                {isActive ? "Live" : isCandidate ? "Candidate" : `v${r.version}`} · {formatDate(r.created_at)}
              </span>
              <span className="k" title={r.signature}>
                {r.signature.slice(0, 12)}…
              </span>
              <span className="s">
                {r.approved_by ? (
                  <span data-testid={`release-approved-by-${r.id}`}>approved by {r.approved_by}</span>
                ) : (
                  <span className="text-ink-2">{isCandidate ? "needs your approval" : "—"}</span>
                )}
                {isActive ? (
                  <>
                    {" "}
                    <span data-testid={`release-active-${r.id}`}>active</span>
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
