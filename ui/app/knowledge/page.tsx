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
      joins: boolean;
      body_templates: boolean;
    }
  >;
  transforms: string[];
  checks: string[];
  stop_conditions: string[];
  rules: { id: string; fixes: string; confidence: number; verification_level: string }[];
  v1_v2_diff: string[];
};

type KnowledgeEntry = {
  vendor: string;
  drift_class: string;
  drift_signature: string;
  rule_id: string;
  patch: unknown[];
  learned_from: { run_id: string; rung: number; tokens: number; cost: string; at: string };
  times_reused: number;
  tokens_saved: number;
  cost_saved: string;
  approvals: number;
};

type KnowledgeConnector = {
  connector_id: string;
  name: string;
  vocabulary: string;
  builds: number;
  confirmed_hypotheses: string[];
  last_built_at: string | null;
};

type Knowledge = {
  entries: KnowledgeEntry[];
  connectors: KnowledgeConnector[];
  counts: Record<string, number>;
};

export default function KnowledgePage() {
  const { data: capabilities, error: capError } = useSWR<Capabilities>("/capabilities", fetcher);
  const { data: knowledge, error: knowledgeError } = useSWR<Knowledge>("/knowledge", fetcher);

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
        The <em>vocabulary</em> is what the connector language can express (it changes only when code
        ships). <em>Learned knowledge</em> is what the engine picked up from verified repairs and builds
        (it grows at runtime and is cleared by a reset).
      </p>

      <section aria-label="Vocabulary" className="mt-8">
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
            {(["v1", "v2"] as const).map((v) => {
              const ver = capabilities.versions[v];
              if (!ver) return null;
              return (
                <div key={v} data-testid={`knowledge-vocabulary-${v}`} className="mb-4">
                  <h3 className="font-mono text-sm font-bold">vocabulary {v}</h3>
                  <p className="mt-1 font-mono text-xs text-ink-2">
                    methods: {ver.methods.join(", ") || "—"} · auth: {ver.auth_types.join(", ") || "—"} ·
                    pagination: {ver.pagination_strategies.join(", ") || "—"} · joins:{" "}
                    {ver.joins ? "yes" : "no"} · body templates: {ver.body_templates ? "yes" : "no"}
                  </p>
                </div>
              );
            })}
            <p className="mt-1 font-mono text-xs text-ink-2" data-testid="knowledge-transforms">
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

      <section aria-label="Learned knowledge" className="mt-8">
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
                  learned at rung {entry.learned_from.rung} ({entry.learned_from.tokens} tokens) ·
                  reused {entry.times_reused}x, saved {entry.tokens_saved} tokens
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
                  vocabulary {conn.vocabulary || "?"} · {conn.confirmed_hypotheses.length} confirmed{" "}
                  {conn.confirmed_hypotheses.length === 1 ? "hypothesis" : "hypotheses"}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}
