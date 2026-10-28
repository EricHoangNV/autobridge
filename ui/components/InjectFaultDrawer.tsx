"use client";

import { useState } from "react";

// Same-origin /api base (REQ-018): the rewrite + middleware forward to control.
import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

const TARGET_OPTIONS = [
  { value: "sim-atlas", label: "AtlasPMS (sim-atlas)" },
  { value: "sim-harbor", label: "HarborPMS (sim-harbor)" },
  { value: "atlas", label: "atlas" },
  { value: "harbor", label: "harbor" },
];

const SCENARIO_OPTIONS = [
  "baseline",
  "rename_guest_id",
  "offset_to_cursor",
  "page_to_cursor",
  "cursor_repeat_token",
  "nested_path_shift",
  "gross_to_net",
  "tz_boundary_shift",
  "status_vocab_change",
  "endpoint_removed",
  "token_revoked",
  "rate_limit_429",
  "transient_503",
  "invalid_patch_trap",
];

// Scenarios the deterministic ladder is not expected to fix on its own:
// auth is revoked or the endpoint is gone, so the run escalates to a human.
const ESCALATING_SCENARIOS = new Set(["endpoint_removed", "token_revoked"]);

type Props = {
  defaultOpen?: boolean;
  onInjected?: (result: unknown) => void;
};

export function InjectFaultDrawer({ defaultOpen, onInjected }: Props) {
  const [open, setOpen] = useState(!!defaultOpen);
  const [target, setTarget] = useState("sim-atlas");
  const [scenarioId, setScenarioId] = useState("rename_guest_id");
  const [seed, setSeed] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  async function handleInject() {
    setBusy(true);
    setResult(null);
    try {
      const base = getBaseUrl().replace(/\/$/, "");
      const payload: Record<string, unknown> = { target, scenario_id: scenarioId };
      if (seed.trim()) {
        const n = Number(seed.trim());
        if (Number.isFinite(n)) payload.seed = n;
      }
      const res = await fetch(`${base}/demo/inject-fault`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.text();
      let pretty = body;
      try {
        pretty = JSON.stringify(JSON.parse(body), null, 2);
      } catch {
        // keep raw
      }
      if (!res.ok) {
        setResult({ ok: false, text: `HTTP ${res.status}: ${pretty}` });
      } else {
        setResult({ ok: true, text: pretty });
        try {
          onInjected?.(JSON.parse(body));
        } catch {
          onInjected?.(body);
        }
      }
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="inject-fault-drawer" className="border-[1.5px] border-dashed border-off bg-band">
      <button
        data-testid="inject-fault-toggle"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
      >
        <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className="border border-rule px-1.5 py-0.5 font-mono text-[11px] font-bold uppercase tracking-[0.08em] text-ink-2">
            Demo
          </span>
          <span className="text-[15px] font-bold text-ink">Inject fault</span>
          <span className="font-mono text-xs text-ink-2">labelled demo action</span>
        </span>
        <span className="shrink-0 font-mono text-xs text-ink-2">{open ? "Hide" : "Show"}</span>
      </button>

      {open ? (
        <div data-testid="inject-fault-panel" className="space-y-4 border-t border-rule px-4 py-4">
          <p className="text-[15px] font-bold text-ink">Inject a fault</p>
          <p className="text-sm text-ink-2">
            Demo only. This changes the simulator, never the engine. Proxies to the simulator admin{" "}
            <code className="bg-ground px-1 py-0.5 font-mono text-xs">POST /admin/scenario</code> via the
            control plane <code className="bg-ground px-1 py-0.5 font-mono text-xs">POST /demo/inject-fault</code>.
            Every request is labelled <code className="bg-ground px-1 py-0.5 font-mono text-xs">demo: true</code> and{" "}
            <code className="bg-ground px-1 py-0.5 font-mono text-xs">labelled: true</code>.
          </p>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-bold text-ink">Target</span>
              <select
                data-testid="inject-fault-target"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className="w-full appearance-none border-[1.5px] border-off bg-ground px-3 py-2.5 font-mono text-sm text-ink focus:border-live focus:outline-none"
              >
                {TARGET_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-sm font-bold text-ink">Scenario</span>
              <select
                data-testid="inject-fault-scenario"
                value={scenarioId}
                onChange={(e) => setScenarioId(e.target.value)}
                className="w-full appearance-none border-[1.5px] border-off bg-ground px-3 py-2.5 font-mono text-sm text-ink focus:border-live focus:outline-none"
              >
                {SCENARIO_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                    {ESCALATING_SCENARIOS.has(s) ? " · escalates" : ""}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="mb-1.5 block text-sm font-bold text-ink">Seed (optional)</span>
              <input
                data-testid="inject-fault-seed"
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
                placeholder="e.g. 42"
                className="w-full border-[1.5px] border-off bg-ground px-3 py-2.5 font-mono text-sm text-ink placeholder:text-ink-2/60 focus:border-live focus:outline-none"
              />
            </label>
          </div>

          <p className="font-mono text-xs text-ink-2">
            {ESCALATING_SCENARIOS.has(scenarioId)
              ? `${scenarioId} usually escalates to a human.`
              : `${scenarioId} is a deterministic repair: expect rung L0/L1 and 0 frontier tokens.`}
          </p>

          <button
            data-testid="inject-fault-submit"
            onClick={handleInject}
            disabled={busy}
            className="btn w-full text-center !text-base disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          >
            {busy ? "Injecting…" : `Inject ${scenarioId}`}
          </button>

          {result ? (
            <div
              data-testid={result.ok ? "inject-fault-success" : "inject-fault-error"}
              className={`border px-3 py-2 ${
                result.ok ? "border-ink bg-ground" : "border-stop bg-band"
              }`}
            >
              <pre
                className={`max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs ${
                  result.ok ? "text-ink" : "text-stop"
                }`}
              >
                {result.text}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
