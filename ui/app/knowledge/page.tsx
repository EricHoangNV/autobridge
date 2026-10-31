"use client";

import useSWR from "swr";

import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

const fetcher = async <T,>(path: string): Promise<T> => {
  const base = getBaseUrl().replace(/\/$/, "");
  const res = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return (await res.json()) as T;
};

type Capabilities = {
  versions: Record<
    string,
    {
      methods: string[];
      auth_types: string[];
      pagination_strategies: string[];
      protocols: string[];
      joins: boolean;
      body_templates: boolean;
      event_feeds: boolean;
      command_templates: boolean;
    }
  >;
  transforms: string[];
  checks: string[];
  stop_conditions: string[];
  rules: { id: string; fixes: string; confidence: number; verification_level: string }[];
  v1_v2_diff: string[];
  v2_v3_diff: string[];
};

type KnowledgeEntry = {
  vendor: string;
  drift_class: string;
  drift_signature: string;
  rule_id: string;
  patch: unknown[];
  learned_from: {
    run_id: string;
    rung: number;
    tokens: number;
    cost: string;
    at: string;
    approval_id?: string;
    actor?: string;
    release_id?: string;
  };
  times_reused: number;
  tokens_saved: number;
  cost_saved: string;
  approvals: number;
};

type KnowledgeConnector = {
  connector_id: string;
  name: string;
  vocabulary: string;
  refused_vocabularies: string[];
  builds: number;
  confirmed_hypotheses: string[];
  last_built_at: string | null;
};

type KnowledgeDecision = {
  id: string;
  at: string | null;
  actor: string;
  kind: string;
  business_decision: boolean;
  learned: boolean;
  connector_id: string;
  connector_name: string;
  repair_run_id: string;
  from_release_id?: string | null;
  to_release_id?: string | null;
  summary: string;
};

type Knowledge = {
  entries: KnowledgeEntry[];
  connectors: KnowledgeConnector[];
  counts: Record<string, number>;
  decisions?: KnowledgeDecision[];
};

type Vocabulary = Capabilities["versions"][string];

// One line per language release, so the chain reads as a timeline rather than an inventory.
const RELEASE_NOTE: Record<string, string> = {
  v1: "REST reads: GET with headers or basic auth",
  v2: "request bodies and joins (POST-style APIs)",
  v3: "event streams over TCP (on-premise devices)",
};

// v1 lists everything; v2 and v3 list only what they add on top of the previous version.
function vocabularyLine(ver: Vocabulary, deltaOnly: boolean): string {
  const lists: [string, string[]][] = [
    ["methods", ver.methods],
    ["auth", ver.auth_types],
    ["pagination", ver.pagination_strategies],
    ["protocols", ver.protocols || []],
  ];
  const flags: [string, boolean][] = [
    ["joins", ver.joins],
    ["body templates", ver.body_templates],
    ["event feeds", ver.event_feeds],
    ["command templates", ver.command_templates],
  ];
  const parts = deltaOnly
    ? [
        ...lists.filter(([, v]) => v.length).map(([k, v]) => `${k}: ${v.join(", ")}`),
        ...flags.filter(([, v]) => v).map(([k]) => k),
      ]
    : [
        ...lists.map(([k, v]) => `${k}: ${v.join(", ") || "—"}`),
        ...flags.map(([k, v]) => `${k}: ${v ? "yes" : "no"}`),
      ];
  return parts.join(" · ");
}

export default function KnowledgePage() {
  const { data: capabilities, error: capError } = useSWR<Capabilities>("/capabilities", fetcher, {
    refreshInterval: 2000,
  });
  const { data: knowledge, error: knowledgeError } = useSWR<Knowledge>("/knowledge", fetcher, {
    refreshInterval: 2000,
  });

  return (
    <div data-testid="knowledge-page" className="px-5 py-8 sm:px-8">
      <p className="font-mono text-[13px] text-ink-2">
        <a href="/connectors" data-testid="back-to-connectors" className="text-ink-2 hover:text-ink hover:underline">
          Connectors
        </a>{" "}
        / what AutoBridge knows
      </p>
      <h1 className="mt-2 font-bold leading-none" style={{ fontSize: "clamp(34px, 3.4vw, 44px)" }}>
        What AutoBridge knows
      </h1>
      <p className="mt-3 max-w-[54ch] text-[17px] text-ink-2">
        The <em>vocabulary</em> is the connector language, versioned like any API: each release adds what
        it can express, and every connector keeps the version it was built with. <em>Learned
        knowledge</em> is what the engine picked up from verified repairs and builds (it grows at runtime
        and is cleared by a reset).
      </p>

      <div className="mt-8 grid gap-10 lg:grid-cols-2" data-testid="knowledge-columns">
        <section aria-label="Vocabulary">
          <h2 className="sec" data-testid="knowledge-vocabulary-title">
            Vocabulary
          </h2>
          {capError ? (
            <p data-testid="knowledge-vocabulary-error" className="text-sm text-stop">
              Vocabulary unavailable.
            </p>
          ) : !capabilities ? (
            <p data-testid="knowledge-vocabulary-loading" className="text-sm text-ink-2" aria-busy="true">
              Loading vocabulary…
            </p>
          ) : (
            <div data-testid="knowledge-vocabulary">
              <ol className="list-none p-0" data-testid="knowledge-vocabulary-chain">
                {(["v1", "v2", "v3"] as const)
                  .filter((v) => capabilities.versions[v])
                  .map((v, idx) => {
                    const ver = capabilities.versions[v];
                    const plus = idx > 0 ? `everything in v${idx}, plus:` : null;
                    const usedBy = (knowledge?.connectors ?? []).filter((c) => c.vocabulary === v).map((c) => c.name);
                    return (
                      <li key={v}>
                        {idx > 0 ? (
                          <p aria-hidden="true" className="py-1 pl-4 font-mono text-sm text-ink-2">
                            ↓
                          </p>
                        ) : null}
                        <div
                          data-testid={`knowledge-vocabulary-${v}`}
                          data-in-use={usedBy.length > 0 ? "yes" : "no"}
                          className={`border px-4 py-3 ${usedBy.length ? "border-ink" : "border-rule opacity-60"}`}
                        >
                          <h3 className="font-mono text-sm font-bold">
                            vocabulary {v}
                            {RELEASE_NOTE[v] ? <span className="font-normal text-ink-2"> · {RELEASE_NOTE[v]}</span> : null}
                          </h3>
                          {plus ? <p className="mt-1 font-mono text-xs text-ink-2">{plus}</p> : null}
                          <p className="mt-1 font-mono text-xs text-ink-2">{vocabularyLine(ver, plus !== null)}</p>
                          <p className="mt-1 font-mono text-xs text-ink" data-testid={`knowledge-vocabulary-${v}-used-by`}>
                            {usedBy.length ? `used by: ${usedBy.join(", ")}` : "no connector built on it yet"}
                          </p>
                        </div>
                      </li>
                    );
                  })}
              </ol>
              <p className="mt-4 font-mono text-xs text-ink-2" data-testid="knowledge-transforms">
                transforms: {capabilities.transforms.join(", ")}
              </p>
              <div className="mt-3">
                <h3 className="font-mono text-sm font-bold">L1 rules</h3>
                <ul className="list-none p-0">
                  {capabilities.rules.map((rule) => (
                    <li key={rule.id} data-testid={`knowledge-rule-${rule.id}`} className="py-1 text-[15px]">
                      <code className="font-mono text-sm">{rule.id}</code>{" "}
                      <span className="text-ink-2">{rule.fixes}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </section>

        <section aria-label="Learned knowledge">
          <h2 className="sec" data-testid="knowledge-learned-title">
            Learned knowledge
          </h2>
          {knowledgeError ? (
            <p data-testid="knowledge-learned-error" className="text-sm text-stop">
              Learned knowledge unavailable.
            </p>
          ) : !knowledge ? (
            <p data-testid="knowledge-learned-loading" className="text-sm text-ink-2" aria-busy="true">
              Loading learned knowledge…
            </p>
          ) : knowledge.entries.length === 0 ? (
            <p data-testid="knowledge-learned-empty" className="text-sm text-ink-2">
              Nothing learned yet. Inject a drift, repair it, approve the fix, and inject it again — the
              second run reuses the first at zero model calls.
            </p>
          ) : (
            <div data-testid="knowledge-learned">
              {knowledge.entries.map((entry, i) => (
                <div
                  key={`${entry.vendor}-${entry.rule_id}-${i}`}
                  data-testid={`knowledge-entry-${i}`}
                  className="border-b border-rule py-3 first:border-t"
                >
                  <p className="font-mono text-sm">
                    <span className="font-bold text-ink">
                      {entry.vendor} / {entry.drift_class}
                    </span>{" "}
                    <span className="text-ink-2">→ {entry.rule_id}</span>
                  </p>
                  <p className="mt-1 font-mono text-xs text-ink-2">
                    learned at rung {entry.learned_from.rung} ({entry.learned_from.tokens} tokens)
                    {entry.learned_from.approval_id ? (
                      <>
                        {" "}· learned from approval {entry.learned_from.approval_id}
                        {entry.learned_from.actor ? ` by ${entry.learned_from.actor}` : ""}
                      </>
                    ) : null}{" "}
                    · reused {entry.times_reused}×, saved {entry.tokens_saved} tokens
                  </p>
                </div>
              ))}
            </div>
          )}
          {knowledge && knowledge.connectors.length > 0 ? (
            <div className="mt-4" data-testid="knowledge-connectors">
              <h3 className="font-mono text-sm font-bold">Connector profiles</h3>
              {knowledge.connectors.map((conn) => (
                <div key={conn.connector_id} data-testid={`knowledge-connector-${conn.connector_id}`} className="py-1 text-[15px]">
                  <code className="font-mono text-sm">{conn.name}</code>{" "}
                  <span className="font-mono text-xs text-ink-2">
                    vocabulary {conn.vocabulary || "?"}
                    {(conn.refused_vocabularies ?? []).length > 0
                      ? ` · ${(conn.refused_vocabularies ?? []).join(", ")} refused`
                      : ""}{" "}
                    · {conn.confirmed_hypotheses.length} confirmed{" "}
                    {conn.confirmed_hypotheses.length === 1 ? "hypothesis" : "hypotheses"}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      </div>

      {/* REQ-049 item 9: every human decision, newest first — sourced from
          the approval/rejection/rollback rows, separate from learned
          knowledge. A reset clears them like everything else. */}
      <section aria-label="Decisions" className="mt-10">
        <h2 className="sec" data-testid="knowledge-decisions-title">
          Decisions
        </h2>
        {knowledgeError ? (
          <p data-testid="knowledge-decisions-error" className="text-sm text-stop">
            Decisions unavailable.
          </p>
        ) : !knowledge ? (
          <p data-testid="knowledge-decisions-loading" className="text-sm text-ink-2" aria-busy="true">
            Loading decisions…
          </p>
        ) : (knowledge.decisions ?? []).length === 0 ? (
          <p data-testid="knowledge-decisions-empty" className="text-sm text-ink-2">
            No operator decisions yet. Approve or reject a repair and it lands here.
          </p>
        ) : (
          <div data-testid="knowledge-decisions">
            {(knowledge.decisions ?? []).map((decision, i) => (
              <div
                key={decision.id}
                data-testid={`knowledge-decision-${i}`}
                className="border-b border-rule py-3 first:border-t"
              >
                <p className="text-[15px]">
                  <span className="font-bold">
                    {decision.kind === "approve" && decision.business_decision
                      ? "Business decision"
                      : decision.kind === "approve"
                        ? `Approved${decision.learned ? " (learned)" : ""}`
                        : decision.kind === "reject"
                          ? "Denied"
                          : decision.kind === "rollback"
                            ? "Rolled back"
                            : decision.kind}
                  </span>{" "}
                  <span className="text-ink-2">{decision.summary}</span>
                </p>
                <p className="mt-1 font-mono text-xs text-ink-2">
                  {decision.at ? new Date(decision.at).toLocaleString() : "—"} · by {decision.actor || "operator"}
                  {decision.connector_name ? ` · ${decision.connector_name}` : ""}{" "}
                  <a className="underline hover:text-ink" href={`/repairs/${encodeURIComponent(decision.repair_run_id)}`}>
                    repair {decision.repair_run_id.slice(0, 8)}
                  </a>
                  {decision.to_release_id ? (
                    <>
                      {" "}·{" "}
                      <a
                        className="underline hover:text-ink"
                        href={`/connectors/${encodeURIComponent(decision.connector_id)}`}
                      >
                        release {decision.to_release_id.slice(0, 8)}
                      </a>
                    </>
                  ) : null}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
