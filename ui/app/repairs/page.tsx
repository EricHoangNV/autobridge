"use client";

import useSWR from "swr";
import Link from "next/link";

function getBaseUrl(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_URL ?? window.location.origin.replace(/:3000$/, ":8101");
  }
  return process.env.NEXT_PUBLIC_API_URL ?? process.env.CONTROL_URL ?? "http://localhost:8101";
}

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl();
  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

type RepairOut = {
  id: string;
  drift_event_ids: string[];
  ladder_level_reached: number;
  candidate_patch?: unknown[] | null;
  rule_id?: string | null;
  hypothesis_ids: string[];
  visible_test_results: unknown[];
  verifier_report_id?: string | null;
  decision: string;
  model_usage: Record<string, unknown>;
  escalation_bundle?: Record<string, unknown> | null;
  created_at: string;
};

function decisionBadge(decision: string) {
  if (decision === "propose") return "bg-emerald-100 text-emerald-800 border-emerald-200";
  if (decision === "escalate") return "bg-red-100 text-red-800 border-red-200";
  if (decision === "reject") return "bg-zinc-100 text-zinc-700 border-zinc-200";
  return "bg-zinc-100 text-zinc-600 border-zinc-200";
}

function levelBadge(level: number) {
  const labels: Record<number, string> = { 0: "L0", 1: "L1", 2: "L2", 3: "L3", 4: "L4" };
  return labels[level] ?? `L${level}`;
}

export default function RepairsPage() {
  const { data: repairs, error, isLoading } = useSWR<RepairOut[]>("/repairs", fetcher, { refreshInterval: 5000 });

  if (isLoading) {
    return (
      <div data-testid="repairs-page" className="py-8 text-center text-sm text-zinc-500">
        Loading repairs…
      </div>
    );
  }

  if (error) {
    return (
      <div data-testid="repairs-page">
        <div data-testid="repairs-error" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          Failed to load repairs: {(error as Error).message}
        </div>
      </div>
    );
  }

  return (
    <div data-testid="repairs-page">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Repairs</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Ladder trace, patch, tests, verifier verdict, model usage — approve, reject or rollback.
          </p>
        </div>
        <a href="/connectors" className="text-xs text-zinc-500 hover:text-zinc-900 hover:underline">
          ← Connectors
        </a>
      </div>

      {!repairs?.length ? (
        <div data-testid="repairs-empty" className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-zinc-500">
          No repairs yet. Inject a fault or trigger a sync that drifts to create one.
        </div>
      ) : (
        <div data-testid="repairs-list" className="overflow-hidden rounded-lg border bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-4 py-2 font-medium">Repair</th>
                <th className="px-4 py-2 font-medium">Decision</th>
                <th className="px-4 py-2 font-medium">Ladder</th>
                <th className="px-4 py-2 font-medium">Rule</th>
                <th className="px-4 py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {repairs.map((r) => (
                <tr key={r.id} data-testid={`repair-row-${r.id}`} className="hover:bg-zinc-50">
                  <td className="px-4 py-3">
                    <Link href={`/repairs/${encodeURIComponent(r.id)}`} className="font-mono text-xs font-medium text-zinc-900 hover:underline">
                      {r.id.slice(0, 8)}
                    </Link>
                    <div className="font-mono text-[11px] text-zinc-400">{r.id.slice(0, 12)}…</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${decisionBadge(r.decision)}`}>
                      {r.decision}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span data-testid={`repair-ladder-${r.id}`} className="inline-flex rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-700">
                      {levelBadge(r.ladder_level_reached)}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-600">{r.rule_id ?? "—"}</td>
                  <td className="px-4 py-3 text-xs text-zinc-500">
                    {(() => {
                      try {
                        return new Date(r.created_at).toLocaleString();
                      } catch {
                        return r.created_at;
                      }
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p data-testid="repairs-count" className="mt-3 text-xs text-zinc-400">
        {repairs?.length ?? 0} repair{(repairs?.length ?? 0) === 1 ? "" : "s"} · polling every 5s
      </p>
    </div>
  );
}
