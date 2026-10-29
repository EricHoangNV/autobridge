"use client";

import { useEffect, useState } from "react";

// Same-origin /api base (REQ-018): the rewrite + middleware forward to control.
import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

type Props = {
  repairId: string;
  decision: string;
  candidatePatch?: unknown[] | null;
  onActionComplete?: () => void;
  /**
   * Connector this repair belongs to (from its drift events). Sent as
   * `connector_id` on approve so the server records the from-release in
   * the approval audit row (the same inference `GET .../approve-preview`
   * describes). Optional so existing callers are unaffected.
   */
  connectorId?: string | null;
  /**
   * True once a patch has passed the verifier (report linked + candidate patch
   * present). While the repair is still climbing the verdict and the action
   * stay hidden: Approve/Reject are disabled with "No patch to approve yet"
   * (REQ-002 slice 3). Defaults to true so existing callers are unaffected.
   */
  patchReady?: boolean;
};

export function RepairActions({ repairId, decision, candidatePatch: _candidatePatch, onActionComplete, patchReady = true, connectorId = null }: Props) {
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

  const canApprove = decision === "propose" && patchReady;
  const canReject = decision === "propose" && patchReady;
  // The climb: no verdict and no action until a patch has passed.
  const awaitingPatch = decision === "propose" && !patchReady;
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
        body: JSON.stringify(connectorId ? { connector_id: connectorId } : {}),
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
    <div data-testid="repair-actions" className="repair-acts repair-past">
      <button
        data-testid="repair-approve-btn"
        onClick={doApprove}
        disabled={!canApprove || busy !== null}
        className={awaitingPatch ? "btn quiet" : "btn"}
      >
        {busy === "approve" ? "Approving…" : awaitingPatch ? "No patch to approve yet" : "Approve release"}
      </button>
      <div className="more">
        <button
          data-testid="repair-reject-btn"
          onClick={doReject}
          disabled={!canReject || busy !== null}
          className="link"
        >
          {busy === "reject" ? "Rejecting…" : "Reject patch"}
        </button>
        <span className="link" aria-hidden="true" style={{ textDecoration: "none" }}>
          ·
        </span>
        <label className="link" style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
          Roll back to
          <input
            data-testid="rollback-target-input"
            value={rollbackTarget}
            onChange={(e) => setRollbackTarget(e.target.value)}
            placeholder="release id"
            className="border border-rule bg-ground px-2 py-1 font-mono text-xs text-ink"
            style={{ width: 130 }}
          />
        </label>
        <button
          data-testid="rollback-btn"
          onClick={doRollback}
          disabled={!canRollback || busy !== null}
          className="link"
        >
          {busy === "rollback" ? "Rolling back…" : "Roll back"}
        </button>
      </div>
      <p data-testid="approving-as" className="text-xs text-ink-2">
        Approving as {operatorName ?? "…"}
      </p>

      {!canApprove ? (
        <p className="text-xs text-ink-2">
          {awaitingPatch
            ? "The verifier has not passed a patch yet — Approve/Reject unlock when it does."
            : `Approve/Reject available only when decision is “propose” (current: ${decision}).`}
        </p>
      ) : null}

      {msg ? (
        <div
          data-testid={msg.type === "error" ? "repair-action-error" : "repair-action-success"}
          className="border px-3 py-2 text-sm"
          style={
            msg.type === "error"
              ? { borderColor: "var(--stop)", color: "var(--stop)" }
              : { borderColor: "var(--rule)", color: "var(--ink)" }
          }
        >
          {msg.text}
        </div>
      ) : null}
    </div>
  );
}
