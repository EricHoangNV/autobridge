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

/** Record layouts, readably (REQ-069 item 6): `{"DR": ["DA", "TI"]}`
 * reads "DR: DA, TI" — never raw JSON. Unparseable values fall back to
 * the raw string, never blank. */
function formatLayouts(raw: string | null): string | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return raw;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return raw;
  const parts: string[] = [];
  for (const [recordType, fields] of Object.entries(parsed)) {
    if (Array.isArray(fields)) parts.push(`${recordType}: ${fields.map(String).join(", ")}`);
    else if (fields !== undefined && fields !== null) parts.push(`${recordType}: ${String(fields)}`);
    else parts.push(recordType);
  }
  return parts.length ? parts.join(" · ") : raw;
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
  // REQ-068 item 7: an edge connector's agent id (e.g. grand-plaza-pabx)
  // never equals the connector id, so matching by connector id alone left
  // the query unfiltered on the relay's global queue (stale "last 11 m
  // ago"). Fall back to the single PABX agent once it is known.
  const matchedAgent = (() => {
    const list = agents?.agents ?? [];
    return (
      list.find((a) => a.agent_id === connectorId) ??
      list.find((a) => a.agent_id === connectorName) ??
      null
    );
  })();
  const agent = (() => {
    const list = agents?.agents ?? [];
    // Link state may fall back to the first agent (one relay, one PABX);
    // the postings below do the same — never the unfiltered global queue
    // once any agent is known.
    return matchedAgent ?? list[0] ?? null;
  })();
  const { data: postings, error: postingsError } = useSWR<{ postings: EdgePosting[] }>(
    agent ? `/edge/postings?agent_id=${encodeURIComponent(agent.agent_id)}` : "/edge/postings",
    fetcher,
    { refreshInterval: 2000 },
  );

  const links = (events ?? []).filter((e) => e.kind === "link");
  const restarts = (events ?? []).filter((e) => e.kind === "restart");
  const latestLink = links[0] ?? null;
  const latestRestart = restarts[0] ?? null;

  const liveUp = agent?.link_up;
  const snapUp = latestLink ? latestLink.payload["link_up"] === true : null;
  const linkUp = liveUp ?? snapUp;
  // REQ-068 item 7: plain state only — the heartbeat detail
  // ("accepted=2 dropped=0 layouts={…}") is debug text, not a state.
  // Layouts ride in their own row. A live agent with a down link is
  // mid-handshake, so the beat reads "re-handshaking" instead of "down".
  const agentUpdatedAt = typeof agent?.updated_at === "string" ? agent.updated_at : null;
  const agentFresh = (() => {
    if (!agentUpdatedAt) return false;
    const t = Date.parse(agentUpdatedAt);
    if (!Number.isFinite(t)) return false;
    return Date.now() - t < 30000;
  })();
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
            ) : agentFresh ? (
              <span style={{ color: "var(--live)" }}>re-handshaking…</span>
            ) : (
              <span style={{ color: "var(--stop)" }}>down</span>
            )}
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
          <dd data-testid="pabx-layout" className="font-mono" title={layouts ?? undefined}>
            {layouts ? (formatLayouts(layouts) ?? layouts) : <span className="text-ink-2">not announced yet</span>}
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
