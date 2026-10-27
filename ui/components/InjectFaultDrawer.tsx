"use client";

import { useState } from "react";

function getBaseUrl(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_URL ?? window.location.origin.replace(/:3000$/, ":8101");
  }
  return process.env.NEXT_PUBLIC_API_URL ?? process.env.CONTROL_URL ?? "http://localhost:8101";
}

const TARGET_OPTIONS = [
  { value: "sim-atlas", label: "sim-atlas (AtlasPMS)" },
  { value: "sim-harbor", label: "sim-harbor (HarborPMS)" },
  { value: "atlas", label: "atlas" },
  { value: "harbor", label: "harbor" },
];

const SCENARIO_OPTIONS = [
  "baseline",
  "rename_guest_id",
  "offset_to_cursor",
  "cursor_repeat_token",
  "nested_path_shift",
  "gross_to_net",
  "tz_boundary_shift",
  "status_vocab_change",
  "endpoint_removed",
  "token_revoked",
  "rate_limit_429",
  "transient_503",
];

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
    <div data-testid="inject-fault-drawer" className="border bg-band">
      <button
        data-testid="inject-fault-toggle"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between border-b bg-amber-50 px-4 py-3 text-left hover:bg-amber-100/70"
        aria-expanded={open}
      >
        <span className="flex items-center gap-2">
          <span className="inline-flex bg-amber-200 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-amber-900">
            Demo
          </span>
          <span className="text-sm font-semibold text-ink">Inject Fault</span>
          <span className="border border-amber-200 bg-band px-1.5 py-0.5 text-[11px] font-medium text-amber-800">
            labelled demo action
          </span>
        </span>
        <span className="text-xs text-ink-2">{open ? "Hide" : "Show"}</span>
      </button>

      {open ? (
        <div data-testid="inject-fault-panel" className="space-y-3 px-4 py-4">
          <p className="text-xs text-ink-2">
            Proxies to the simulator admin <code className="bg-ground px-1 py-0.5 font-mono text-[11px]">POST /admin/scenario</code> via the
            control plane <code className="bg-ground px-1 py-0.5 font-mono text-[11px]">POST /demo/inject-fault</code>. Every request is
            labelled <code className="bg-ground px-1 py-0.5 font-mono text-[11px]">demo: true</code> and{" "}
            <code className="bg-ground px-1 py-0.5 font-mono text-[11px]">labelled: true</code>.
          </p>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-ink-2">Target</span>
              <select
                data-testid="inject-fault-target"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className="w-full border border-rule px-2 py-1.5 text-xs"
              >
                {TARGET_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1">
              <span className="text-xs font-medium text-ink-2">Scenario</span>
              <select
                data-testid="inject-fault-scenario"
                value={scenarioId}
                onChange={(e) => setScenarioId(e.target.value)}
                className="w-full border border-rule px-2 py-1.5 text-xs"
              >
                {SCENARIO_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1">
              <span className="text-xs font-medium text-ink-2">Seed (optional)</span>
              <input
                data-testid="inject-fault-seed"
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
                placeholder="e.g. 42"
                className="w-full border border-rule px-2 py-1.5 font-mono text-xs"
              />
            </label>
          </div>

          <button
            data-testid="inject-fault-submit"
            onClick={handleInject}
            disabled={busy}
            className="bg-live px-4 py-2 text-sm font-medium text-live-ink hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Injecting…" : "Inject fault"}
          </button>

          {result ? (
            <div
              data-testid={result.ok ? "inject-fault-success" : "inject-fault-error"}
              className={`border px-3 py-2 ${result.ok ? "border-emerald-200 bg-emerald-50" : "border-stop bg-band"}`}
            >
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-ink">
                {result.text}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
