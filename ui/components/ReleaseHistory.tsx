"use client";

import type { ReleaseOut } from "@/lib/api/client";

type Props = {
  releases: ReleaseOut[];
  activeReleaseId?: string | null;
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function ReleaseHistory({ releases, activeReleaseId }: Props) {
  if (!releases.length) {
    return (
      <div data-testid="release-history-empty" className="border border-dashed px-4 py-6 text-center text-sm text-ink-2">
        No releases yet. Trigger a build to create the first release.
      </div>
    );
  }

  // Show newest first
  const sorted = [...releases].sort((a, b) => b.version - a.version);

  return (
    <div data-testid="release-history" className="overflow-hidden border bg-band">
      <table data-testid="release-history-table" className="w-full text-left text-sm">
        <thead className="bg-ground text-xs uppercase tracking-wide text-ink-2">
          <tr>
            <th className="px-4 py-2 font-medium">Version</th>
            <th className="px-4 py-2 font-medium">Release ID</th>
            <th className="px-4 py-2 font-medium">Signature</th>
            <th className="px-4 py-2 font-medium">Approved by</th>
            <th className="px-4 py-2 font-medium">Created</th>
            <th className="px-4 py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {sorted.map((r) => {
            const isActive = r.id === activeReleaseId;
            return (
              <tr
                key={r.id}
                data-testid={`release-row-${r.id}`}
                className={isActive ? "bg-emerald-50/60" : "hover:bg-ground"}
              >
                <td className="px-4 py-2 font-mono text-xs font-medium">v{r.version}</td>
                <td className="px-4 py-2 font-mono text-xs text-ink-2" title={r.id}>
                  {r.id.slice(0, 8)}…
                </td>
                <td className="px-4 py-2 font-mono text-xs text-ink-2" title={r.signature}>
                  {r.signature.slice(0, 12)}…
                </td>
                <td className="px-4 py-2 text-xs text-ink-2">
                  {r.approved_by ? (
                    <span data-testid={`release-approved-by-${r.id}`}>approved by {r.approved_by}</span>
                  ) : (
                    <span className="text-ink-2">—</span>
                  )}
                </td>
                <td className="px-4 py-2 text-xs text-ink-2">{formatDate(r.created_at)}</td>
                <td className="px-4 py-2">
                  {isActive ? (
                    <span
                      data-testid={`release-active-${r.id}`}
                      className="inline-flex bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800"
                    >
                      active
                    </span>
                  ) : (
                    <span className="text-xs text-ink-2">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
