"use client";

import Link from "next/link";
import useSWR from "swr";
import {
  ConnectorsTable,
  frontierTokensOf,
  groupRepairsByConnector,
} from "@/components/ConnectorsTable";
import type {
  ConnectorOut,
  ConnectorDetail,
  DiscoveryRecordOut,
  DriftEventOut,
  ModelUsageResponse,
  RepairRunOut,
} from "@/lib/api/client";
import { DiscoveryList } from "@/components/DiscoveryList";

// Same-origin /api base (REQ-018): the rewrite + middleware forward to control.
import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl();
  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

function formatDayTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return sameDay ? `today ${time}` : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
  } catch {
    return iso;
  }
}

export default function ConnectorsPage() {
  const { data: connectors, error: connError, isLoading: connLoading } = useSWR<ConnectorOut[]>(
    "/connectors",
    fetcher,
    { refreshInterval: 2000, revalidateOnFocus: true }
  );

  const { data: modelUsage } = useSWR<ModelUsageResponse>("/metrics/model-usage", fetcher, {
    refreshInterval: 10000,
  });

  // Fetch details for each connector to populate active release / last sync / drift.
  // In Compose this is N+1 but the list is tiny (plan: two simulators).
  const detailKeys = (connectors ?? []).map((c) => `/connectors/${encodeURIComponent(c.id)}`);
  // We fetch details via a single combined key to keep SWR simple.
  const { data: detailsMap } = useSWR<Record<string, ConnectorDetail>>(
    connectors?.length ? `details:${detailKeys.join(",")}` : null,
    async () => {
      const entries = await Promise.all(
        (connectors ?? []).map(async (c) => {
          try {
            const d = await fetcher<ConnectorDetail>(`/connectors/${encodeURIComponent(c.id)}`);
            return [c.id, d] as const;
          } catch {
            return [c.id, null] as const;
          }
        })
      );
      return Object.fromEntries(entries) as Record<string, ConnectorDetail>;
    },
    { refreshInterval: 2000 }
  );

  const { data: discoveries } = useSWR<DiscoveryRecordOut[]>("/discovery", fetcher, {
    refreshInterval: 10000,
  });

  // Repairs + drift events resolve "who waits" and the recent-repairs list.
  const { data: repairs } = useSWR<RepairRunOut[]>("/repairs", fetcher, { refreshInterval: 2000 });
  const { data: drifts } = useSWR<DriftEventOut[]>("/drift", fetcher, { refreshInterval: 2000 });

  const repairsByConnector =
    repairs && drifts ? groupRepairsByConnector(repairs, drifts) : undefined;
  // REQ-048 item 2: the header counts from the same connector-state source
  // as the rows — connectors in `error` (needing a decision) — never "nothing
  // waiting" above an ■ Error row.
  const errored = (connectors ?? []).filter((c) => (c.state ?? "degrading") === "error");
  const driftToConnector = new Map((drifts ?? []).map((d) => [d.id, d.connector_id]));
  const connectorName = (id: string) =>
    (connectors ?? []).find((c) => c.id === id)?.name ?? id.slice(0, 8);
  const recentRepairs = [...(repairs ?? [])]
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
    .slice(0, 5);

  const headline = (() => {
    const n = connectors?.length ?? 0;
    const nouns = `${n} connector${n === 1 ? "" : "s"}`;
    const tokens =
      modelUsage != null
        ? ` ${(modelUsage.frontier_tokens + modelUsage.small_tokens).toLocaleString()} model tokens.`
        : "";
    return (
      <>
        {nouns}.{" "}
        {errored.length ? (
          <Link data-testid="connectors-waiting-link" href={`/connectors/${encodeURIComponent(errored[0].id)}`}>
            <b>{errored.length} waiting for you.</b>
          </Link>
        ) : (
          <b>nothing waiting for you.</b>
        )}
        {tokens}
      </>
    );
  })();

  return (
    <div data-testid="connectors-page">
      <section className="conn-head">
        <div>
          <h1>Connectors</h1>
          {!connLoading && !connError && connectors ? (
            <p className="conn-line num" data-testid="connectors-headline">{headline}</p>
          ) : null}
        </div>
        <Link href="/connectors/new" data-testid="new-connector-link" className="btn quiet">
          Build a connector
        </Link>
      </section>

      {connLoading ? (
        <p data-testid="connectors-loading" className="px-10 py-8 text-center text-sm text-ink-2">Loading connectors…</p>
      ) : connError ? (
        <div
          data-testid="connectors-error"
          className="mx-10 border border-stop bg-band px-4 py-3 text-sm text-stop"
        >
          Failed to load connectors: {(connError as Error).message}
        </div>
      ) : !(connectors ?? []).length ? (
        <section data-testid="connectors-empty" className="cempty" aria-label="No connectors yet">
          <div>
            <h2>No connectors yet</h2>
            <p>Point AutoBridge at an API&apos;s docs and a sandbox, and it builds a connector you can read, test and approve.</p>
            <Link href="/connectors/new" className="btn">
              Build a connector
            </Link>
          </div>
          <figure className="ghost" aria-label="The repair ladder every connector gets: L0 knowledge and L1 rules cost 0 tokens; L2 and L3 use models; L4 is you.">
            <ol aria-hidden="true">
              <li><b>L0</b><span>Knowledge</span><em>0 tokens</em></li>
              <li><b>L1</b><span>Rules</span><em>0 tokens</em></li>
              <li><b>L2</b><span>Small model</span><em>budgeted</em></li>
              <li><b>L3</b><span>Frontier model</span><em>budgeted</em></li>
              <li><b>L4</b><span>You</span><em>approve</em></li>
            </ol>
            <figcaption>Every connector gets this repair ladder. Known API changes stop at L0 or L1, for zero model tokens.</figcaption>
          </figure>
        </section>
      ) : (
        <ConnectorsTable
          connectors={connectors ?? []}
          detailsById={detailsMap ?? {}}
          modelUsage={modelUsage ?? null}
          repairsByConnector={repairsByConnector}
        />
      )}

      <DiscoveryList records={discoveries ?? []} />

      {(connectors?.length ?? 0) > 0 ? (
        <p data-testid="connectors-count" className="num mt-3 px-10 text-xs text-ink-2">
          {connectors?.length} connector{(connectors?.length ?? 0) === 1 ? "" : "s"} · polling every 2s
        </p>
      ) : null}

      {(connectors?.length ?? 0) > 0 && recentRepairs.length > 0 ? (
        <section className="recent" aria-label="Recent repairs">
          <h2 className="sec">Recent repairs</h2>
          <ul className="reps">
            {recentRepairs.map((r) => {
              const cid = (r.drift_event_ids ?? [])
                .map((id) => driftToConnector.get(id))
                .find((v): v is string => typeof v === "string");
              const who = cid ? connectorName(cid) : "Unknown connector";
              const tokens = frontierTokensOf(r);
              return (
                <li key={r.id}>
                  <span className={r.ladder_level_reached >= 3 ? "lv l3 num" : "lv num"}>
                    L{r.ladder_level_reached}
                  </span>
                  <span className="what">
                    <Link href={`/repairs/${encodeURIComponent(r.id)}`}>
                      {who} · {r.rule_id ?? r.drift_event_ids[0]?.slice(0, 8) ?? "repair"}
                    </Link>
                    <span>
                      {r.decision === "propose" ? "Waiting for you. " : ""}
                      {r.rule_id ? `Rule ${r.rule_id}. ` : ""}
                      {r.decision}
                    </span>
                  </span>
                  <span className="when num text-sm text-ink-2">{formatDayTime(r.created_at)}</span>
                  <span className="tk num">
                    {tokens.toLocaleString()}
                    <small>frontier tokens</small>
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
