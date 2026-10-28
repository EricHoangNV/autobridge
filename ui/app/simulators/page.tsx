"use client";

import useSWR from "swr";

import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";
import type { SimulatorInfo, SimulatorsOut } from "@/lib/api/client";

// Same-origin /api base (REQ-018): the rewrite + middleware forward to control.
const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl().replace(/\/$/, "");
  const res = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

function SimulatorRow({ sim }: { sim: SimulatorInfo }) {
  const usedBy = sim.connectors ?? [];
  return (
    <li
      data-testid={`simulators-row-${sim.id}`}
      className="border-b border-rule py-4 last:border-b-0"
    >
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h3 className="font-mono text-base font-bold" data-testid={`simulators-name-${sim.id}`}>
          {sim.display_name}
        </h3>
        <span
          data-testid={`simulators-live-${sim.id}`}
          className={`font-mono text-xs font-bold ${sim.live ? "text-live" : "text-ink-2"}`}
        >
          {sim.live ? "up" : "down"}
        </span>
        <span className="font-mono text-xs text-ink-2">{sim.api_style}</span>
      </div>
      <dl className="mt-2 grid gap-x-8 gap-y-1 font-mono text-xs text-ink-2 sm:grid-cols-2">
        <div className="flex gap-2">
          <dt className="shrink-0 font-bold">docs</dt>
          <dd>
            <a
              data-testid={`simulators-docs-${sim.id}`}
              href={sim.developers_url}
              target="_blank"
              rel="noreferrer"
              className="hover:text-ink hover:underline"
            >
              {sim.developers_url.replace(/^https?:\/\//, "")}/developers
            </a>{" "}
            <span title={sim.docs_source}>(source: {sim.docs_source})</span>
          </dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 font-bold">public port</dt>
          <dd data-testid={`simulators-port-${sim.id}`}>{sim.public_port}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 font-bold">active fault</dt>
          <dd data-testid={`simulators-fault-${sim.id}`}>{sim.active_scenario ?? "none"}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 font-bold">used by</dt>
          <dd data-testid={`simulators-usedby-${sim.id}`}>
            {usedBy.length > 0 ? usedBy.map((c) => c.name).join(", ") : "no connectors yet"}
          </dd>
        </div>
      </dl>
      {sim.active_scenario ? (
        <p className="mt-1 font-mono text-xs text-stop">
          fault <span className="font-bold">{sim.active_scenario}</span> is active on {sim.display_name} —
          see <a href="/repairs" className="underline">Repairs</a> for the climb.
        </p>
      ) : null}
    </li>
  );
}

export default function SimulatorsPage() {
  const { data, error } = useSWR<SimulatorsOut>("/demo/simulators", fetcher);

  return (
    <div data-testid="simulators-page" className="px-5 py-8 sm:px-8">
      <p className="font-mono text-[13px] text-ink-2">
        <a href="/connectors" data-testid="back-to-connectors" className="text-ink-2 hover:text-ink hover:underline">
          Connectors
        </a>{" "}
        / system status
      </p>
      <h1 className="mt-2 font-bold leading-none" style={{ fontSize: "clamp(34px, 3.4vw, 44px)" }}>
        Simulators
      </h1>
      <p className="mt-3 max-w-[60ch] text-[17px] text-ink-2">
        Every simulated vendor, from the one registry. Up/down is probed server-side through the
        control plane — the browser never calls a simulator directly.
      </p>

      <section aria-label="Simulators" className="mt-8">
        {error ? (
          <p data-testid="simulators-error" className="text-sm text-stop">
            Simulators unavailable.
          </p>
        ) : !data ? (
          <p data-testid="simulators-loading" className="text-sm text-ink-2" aria-busy="true">
            Loading simulators…
          </p>
        ) : (
          <ul data-testid="simulators-list" className="list-none border-t border-rule p-0">
            {data.simulators.map((sim) => (
              <SimulatorRow key={sim.id} sim={sim} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
