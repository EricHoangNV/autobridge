"use client";

type DriftEvent = {
  id: string;
  connector_id: string;
  drift_class: string;
  severity: string;
  evidence_ids: string[];
  source_sync_run_id?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

type EvidenceRecord = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  parent_ids: string[];
  run_id: string;
  created_at: string;
};

type Props = {
  driftEvents: DriftEvent[];
  evidenceRecords?: EvidenceRecord[] | null;
  loading?: boolean;
};

function driftClassColor(driftClass: string): string {
  const v = driftClass.toLowerCase();
  if (v.includes("capability_removed") || v.includes("auth_revoked") || v.includes("blocked")) return "bg-band text-stop border-stop";
  if (v.includes("rate_limit") || v.includes("transient")) return "bg-amber-100 text-amber-800 border-amber-200";
  if (v.includes("semantic") || v.includes("gross_to_net")) return "bg-violet-100 text-violet-800 border-violet-200";
  return "bg-ground text-ink border-rule";
}

export function DriftEvidenceSection({ driftEvents, evidenceRecords, loading }: Props) {
  if (loading) {
    return (
      <div data-testid="drift-evidence-loading" className="border bg-band px-4 py-6 text-center text-sm text-ink-2">
        Loading drift evidence…
      </div>
    );
  }

  if (!driftEvents.length) {
    return (
      <div data-testid="drift-evidence-empty" className="border border-dashed px-4 py-6 text-center text-sm text-ink-2">
        No drift events linked to this repair.
      </div>
    );
  }

  return (
    <div data-testid="drift-evidence-section" className="space-y-4">
      {driftEvents.map((d) => (
        <div key={d.id} data-testid={`drift-event-${d.id}`} className="overflow-hidden border bg-band">
          <div className="flex flex-wrap items-center gap-2 border-b bg-ground px-4 py-3">
            <span
              data-testid={`drift-class-${d.id}`}
              className={`inline-flex border px-2.5 py-0.5 text-xs font-medium ${driftClassColor(d.drift_class)}`}
            >
              {d.drift_class}
            </span>
            <span
              data-testid={`drift-severity-${d.id}`}
              className="inline-flex bg-ground px-2 py-0.5 text-xs text-ink-2"
            >
              {d.severity}
            </span>
            <span data-testid={`drift-status-${d.id}`} className="text-xs text-ink-2">
              {d.status}
            </span>
            <span className="ml-auto font-mono text-xs text-ink-2">{d.id.slice(0, 8)}</span>
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 px-4 py-3 text-xs">
            <div>
              <dt className="text-ink-2">Source sync run</dt>
              <dd className="font-mono text-ink-2">{d.source_sync_run_id ? d.source_sync_run_id.slice(0, 8) : "—"}</dd>
            </div>
            <div>
              <dt className="text-ink-2">Created</dt>
              <dd className="text-ink-2">{(() => { try { return new Date(d.created_at).toLocaleString(); } catch { return d.created_at; } })()}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-ink-2">Evidence IDs ({d.evidence_ids.length})</dt>
              <dd data-testid={`drift-evidence-ids-${d.id}`} className="mt-1 flex flex-wrap gap-1">
                {d.evidence_ids.length ? (
                  d.evidence_ids.map((eid) => (
                    <span key={eid} className="inline-flex bg-ground px-1.5 py-0.5 font-mono text-[11px] text-ink-2">
                      {eid.slice(0, 8)}
                    </span>
                  ))
                ) : (
                  <span className="text-ink-2">none</span>
                )}
              </dd>
            </div>
          </dl>

          {/* Evidence payloads for this drift (if fetched) */}
          {evidenceRecords?.length ? (
            <div className="border-t">
              <div className="bg-ground px-4 py-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-2">
                Evidence records ({evidenceRecords.length})
              </div>
              <ul className="divide-y divide-rule">
                {evidenceRecords
                  .filter((e) => d.evidence_ids.includes(e.id))
                  .map((e) => (
                    <li key={e.id} data-testid={`evidence-record-${e.id}`} className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        <span className="inline-flex bg-sky-50 px-1.5 py-0.5 text-[11px] font-medium text-sky-700">{e.kind}</span>
                        <span className="font-mono text-[11px] text-ink-2">{e.id.slice(0, 8)}</span>
                      </div>
                      <pre className="mt-1 max-h-32 overflow-auto bg-ground p-2 font-mono text-xs text-ink-2">
                        {JSON.stringify(e.payload, null, 2)}
                      </pre>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
