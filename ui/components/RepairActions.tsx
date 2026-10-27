"use client";

import { useEffect, useState } from "react";

function getBaseUrl(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_URL ?? window.location.origin.replace(/:3000$/, ":8101");
  }
  return process.env.NEXT_PUBLIC_API_URL ?? process.env.CONTROL_URL ?? "http://localhost:8101";
}

type Props = {
  repairId: string;
  decision: string;
  candidatePatch?: unknown[] | null;
  onActionComplete?: () => void;
};

export function RepairActions({ repairId, decision, candidatePatch: _candidatePatch, onActionComplete }: Props) {
  // Server-set operator (GET /operator); approvals are recorded under this name.
  const [operatorName, setOperatorName] = useState<string | null>(null);
  const [busy, setBusy] = useState<"approve" | "reject" | "rollback" | null>(null);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function loadOperator() {
      try {
        const base = getBaseUrl().replace(/\/$/, "");
        const res = await fetch(`${base}/operator`);
        if (!res.ok) throw new Error(`operator lookup failed (${res.status})`);
        const data = await res.json();
        if (!cancelled) setOperatorName(typeof data?.name === "string" && data.name ? data.name : "operator");
      } catch {
        if (!cancelled) setOperatorName("operator");
      }
    }
    loadOperator();
    return () => {
      cancelled = true;
    };
  }, []);

  const canApprove = decision === "propose";
  const canReject = decision === "propose";
  // Rollback is available when there was a prior release (we allow it always; backend validates)
  const canRollback = true;

  async function doApprove() {
    setBusy("approve");
    setMsg(null);
    try {
      const base = getBaseUrl().replace(/\/$/, "");
      const res = await fetch(`${base}/repairs/${encodeURIComponent(repairId)}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Approve failed (${res.status}): ${body}`);
      }
      const data = await res.json();
      setMsg({ type: "success", text: `Approved — new active release ${String(data.to_release_id ?? data.id ?? "").slice(0, 8)}` });
      onActionComplete?.();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  async function doReject() {
    setBusy("reject");
    setMsg(null);
    try {
      const base = getBaseUrl().replace(/\/$/, "");
      const res = await fetch(`${base}/repairs/${encodeURIComponent(repairId)}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Reject failed (${res.status}): ${body}`);
      }
      setMsg({ type: "success", text: "Rejected — repair dismissed." });
      onActionComplete?.();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  async function doRollback() {
    if (!rollbackTarget.trim()) {
      setMsg({ type: "error", text: "Rollback target release id is required." });
      return;
    }
    setBusy("rollback");
    setMsg(null);
    try {
      const base = getBaseUrl().replace(/\/$/, "");
      // Rollback is POST /releases/{release_id}/rollback — we need a release id.
      // Use rollbackTarget as both path param and body target (rollback to that release via itself as anchor is not valid;
      // instead we POST to that release's own rollback endpoint).
      // The API is POST /releases/{release_id}/rollback with body.target_release_id
      const res = await fetch(`${base}/releases/${encodeURIComponent(rollbackTarget.trim())}/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_release_id: rollbackTarget.trim(), repair_run_id: repairId }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Rollback failed (${res.status}): ${body}`);
      }
      const data = await res.json();
      const newId = data.new_release?.id ?? data.id ?? "";
      setMsg({ type: "success", text: `Rolled back — new release ${String(newId).slice(0, 8)}` });
      onActionComplete?.();
    } catch (err) {
      setMsg({ type: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-testid="repair-actions" className="border bg-band">
      <div className="border-b bg-ground px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-2">Actions</h3>
      </div>

      <div className="space-y-3 px-4 py-3">
        <div className="flex flex-wrap gap-2">
          <button
            data-testid="repair-approve-btn"
            onClick={doApprove}
            disabled={!canApprove || busy !== null}
            className="bg-live px-4 py-2 text-sm font-medium text-live-ink hover:opacity-90 disabled:opacity-50"
          >
            {busy === "approve" ? "Approving…" : "Approve"}
          </button>
          <button
            data-testid="repair-reject-btn"
            onClick={doReject}
            disabled={!canReject || busy !== null}
            className="border border-rule bg-band px-4 py-2 text-sm font-medium text-ink-2 hover:bg-ground disabled:opacity-50"
          >
            {busy === "reject" ? "Rejecting…" : "Reject"}
          </button>
        </div>
        <p data-testid="approving-as" className="text-xs text-ink-2">
          Approving as {operatorName ?? "…"}
        </p>

        {!canApprove ? (
          <p className="text-xs text-ink-2">Approve/Reject available only when decision is “propose” (current: {decision}).</p>
        ) : null}

        {/* Rollback */}
        <div className="border-t pt-3">
          <h4 className="text-xs font-medium text-ink-2">Rollback</h4>
          <p className="mt-1 text-xs text-ink-2">Restore a prior release by id (creates a new release copying its spec).</p>
          <div className="mt-2 flex gap-2">
            <input
              data-testid="rollback-target-input"
              value={rollbackTarget}
              onChange={(e) => setRollbackTarget(e.target.value)}
              placeholder="target release id"
              className="flex-1 border border-rule px-2 py-1 font-mono text-xs"
            />
            <button
              data-testid="rollback-btn"
              onClick={doRollback}
              disabled={!canRollback || busy !== null}
              className="border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
            >
              {busy === "rollback" ? "Rolling back…" : "Rollback"}
            </button>
          </div>
        </div>

        {msg ? (
          <div
            data-testid={msg.type === "error" ? "repair-action-error" : "repair-action-success"}
            className={`border px-3 py-2 text-sm ${msg.type === "error" ? "border-stop bg-band text-stop" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}
          >
            {msg.text}
          </div>
        ) : null}
      </div>
    </div>
  );
}
