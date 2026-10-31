"use client";

import useSWR from "swr";

import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";
import { ageAgo, useNow } from "@/lib/use-now";

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl().replace(/\/$/, "");
  const res = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

type PabxEvent = {
  id: string;
  connector_id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type EdgeAgent = {
  agent_id: string;
  link_up?: boolean;
  link_detail?: string;
  outbox_depth?: number;
  inbox_depth?: number;
  updated_at?: string;
  [key: string]: unknown;
};

type EdgePosting = {
  idempotency_key: string;
  agent_id: string;
  record_type: string;
  received_at: string;
  occurred_at?: string | null;
  [key: string]: unknown;
};

function strOf(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

/** Record layouts ride in the heartbeat detail as `layouts={...}` (poller). */
function layoutsFromDetail(detail: string | null): string | null {
  if (!detail) return null;
  const i = detail.indexOf("layouts=");
  if (i < 0) return null;
  return detail.slice(i + "layouts=".length).trim() || null;
}

function layoutsFromAgent(agent: EdgeAgent | null): string | null {
  if (!agent) return null;
  for (const key of ["peer_layouts", "layouts", "record_layout", "record_layouts"]) {
    const v = agent[key];
    if (v !== undefined && v !== null) {
      try {
        return typeof v === "string" ? v : JSON.stringify(v);
      } catch {
        return String(v);
      }
    }
  }
  return layoutsFromDetail(strOf(agent.link_detail));
}

export function PabxPanel({ connectorId, connectorName }: { connectorId: string; connectorName: string }) {
  const now = useNow(1000);
  const { data: events } = useSWR<PabxEvent[]>(
    `/demo/pabx-events?connector_id=${encodeURIComponent(connectorId)}`,
    fetcher,
    { refreshInterval: 2000 },
  );
  const { data: agents } = useSWR<{ agents: EdgeAgent[] }>("/edge/agents", fetcher, { refreshInterval: 2000 });
  // REQ-067 item 8: postings are this connector's own, not the relay's
  // global queue — filter by the agent once it is known, so the count and
  // the "last received" timestamp belong to this connector and visibly
  // resume after the link comes back.
  const matchedAgent = (() => {
    const list = agents?.agents ?? [];
    return (
      list.find((a) => a.agent_id === connectorId) ??
      list.find((a) => a.agent_id === connectorName) ??
      null
    );
  })();
  const { data: postings, error: postingsError } = useSWR<{ postings: EdgePosting[] }>(
    matchedAgent ? `/edge/postings?agent_id=${encodeURIComponent(matchedAgent.agent_id)}` : "/edge/postings",
    fetcher,
    { refreshInterval: 2000 },
  );

  const links = (events ?? []).filter((e) => e.kind === "link");
  const restarts = (events ?? []).filter((e) => e.kind === "restart");
  const latestLink = links[0] ?? null;
  const latestRestart = restarts[0] ?? null;

  const agent = (() => {
    const list = agents?.agents ?? [];
    // Link state may fall back to the first agent (one relay, one PABX);
    // the postings above never do — they stay unfiltered until matched.
    return matchedAgent ?? list[0] ?? null;
  })();

  const liveUp = agent?.link_up;
  const snapUp = latestLink ? latestLink.payload["link_up"] === true : null;
  const linkUp = liveUp ?? snapUp;
  const linkDetail = strOf(agent?.link_detail) ?? strOf(latestLink?.payload["link_detail"]);
  const layouts = layoutsFromAgent(agent) ?? layoutsFromDetail(linkDetail);

  const postingList = postings?.postings ?? [];
  const lastPosting = postingList.reduce<string | null>(
    (acc, p) => (!acc || (p.received_at ?? "") > acc ? (p.received_at ?? acc) : acc),
    null,
  );

  const restartPayload = (latestRestart?.payload ?? {}) as Record<string, unknown>;
  const num = (v: unknown) => (typeof v === "number" ? v.toLocaleString("en-US") : "—");

  return (
    <section data-testid="pabx-panel" aria-label="PABX link panel" className="mt-6 border border-rule px-4 py-3">
      <h2 className="sec" data-testid="pabx-panel-title">
        PABX link
      </h2>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-ink-2">Link state</dt>
          <dd data-testid="pabx-link-state" className="font-mono">
            {linkUp === null || linkUp === undefined ? (
              "unknown yet"
            ) : linkUp ? (
              <span className="text-ink">up</span>
            ) : (
              <span style={{ color: "var(--stop)" }}>down</span>
            )}
            {linkDetail ? <span className="text-ink-2"> · {linkDetail}</span> : null}
          </dd>
        </div>
        <div>
          <dt className="text-ink-2">Postings arriving</dt>
          <dd data-testid="pabx-postings" className="num font-mono">
            {postingsError ? (
              <span className="text-ink-2">relay unreachable</span>
            ) : postings ? (
              <>
                {postingList.length.toLocaleString("en-US")} received
                {lastPosting ? <span className="text-ink-2"> · last {ageAgo(lastPosting, now) ?? lastPosting}</span> : null}
              </>
            ) : (
              "loading…"
            )}
          </dd>
        </div>
        <div>
          <dt className="text-ink-2">Record layout</dt>
          <dd data-testid="pabx-layout" className="font-mono">
            {layouts ?? <span className="text-ink-2">not announced yet</span>}
          </dd>
        </div>
        <div>
          <dt className="text-ink-2">Last restart</dt>
          <dd data-testid="pabx-restart" className="num font-mono">
            {latestRestart ? (
              <>
                redelivered {num(restartPayload["redelivered"])} · lost {num(restartPayload["lost"])} · duplicated{" "}
                {num(restartPayload["duplicated"])}
              </>
            ) : (
              <span className="text-ink-2">no restart recorded</span>
            )}
          </dd>
        </div>
      </dl>
    </section>
  );
}
