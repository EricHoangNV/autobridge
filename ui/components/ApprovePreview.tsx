"use client";

import useSWR from "swr";

import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl().replace(/\/$/, "");
  const res = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

export type ApprovePreview = {
  repair_run_id: string;
  approvable: boolean;
  reason: string;
  approve_lines: string[];
  reject_lines: string[];
  facts: Record<string, unknown>;
};

/**
 * "What happens when you click Approve" (REQ-032 §4), generated from the
 * server's real approve path (`GET /repairs/{id}/approve-preview`) — never
 * hard-coded text. When the server's behaviour differs, this text follows
 * it because it is the server describing itself.
 */
export function ApprovePreviewSection({ repairId }: { repairId: string }) {
  const { data: preview, error } = useSWR<ApprovePreview>(
    `/repairs/${encodeURIComponent(repairId)}/approve-preview`,
    fetcher,
    { refreshInterval: 2000 },
  );

  if (error) {
    return (
      <p data-testid="approve-preview-error" className="text-sm text-ink-2">
        Approve preview unavailable: {(error as Error).message}
      </p>
    );
  }
  if (!preview) {
    return (
      <p data-testid="approve-preview-loading" className="text-sm text-ink-2" aria-busy="true">
        Loading what Approve will do…
      </p>
    );
  }
  const prior = preview.facts?.["approval"] as Record<string, unknown> | null | undefined;
  return (
    <div data-testid="approve-preview">
      {prior && typeof prior === "object" && (prior as Record<string, unknown>)["decision"] === "approve" ? (
        <p data-testid="approve-preview-prior" className="mb-2 text-sm">
          Already approved by {String((prior as Record<string, unknown>)["actor"] ?? "operator")}. Approving again
          re-activates the same release.
        </p>
      ) : null}
      {!preview.approvable ? (
        <p data-testid="approve-preview-blocked" className="text-sm">
          {preview.reason || "Approve is not available for this repair."}
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="mb-1 text-sm font-semibold">What happens when you click Approve</h3>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {preview.approve_lines.map((line, i) => (
                <li key={i} data-testid={`approve-preview-line-${i}`}>
                  {line}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="mb-1 text-sm font-semibold">If you click Reject</h3>
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {preview.reject_lines.map((line, i) => (
                <li key={i} data-testid={`reject-preview-line-${i}`}>
                  {line}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
