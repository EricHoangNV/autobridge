/**
 * REQ-058 — "Download capability request": the engine drafts the gap.
 *
 * When a vocabulary refuses the docs, the build page offers the refusal as
 * a Markdown file shaped like an entry in mds/REQUESTS.MD, so
 * "refused → new language version" needs no human writing in between.
 *
 * Deterministic: the same facts in give the same file out (no model call —
 * deck 3 stays "0 model calls"). Everything comes from the refused build's
 * vocabulary-check result, the docs text already on the page, and
 * GET /capabilities. Secrets: only the credential_ref NAME is included,
 * never a value (the browser never holds values).
 */

/** One vocabulary version's expressible surface, as GET /capabilities reports it. */
export type CapabilityVersionFacts = {
  methods: string[];
  auth_types: string[];
  pagination_strategies: string[];
  protocols: string[];
  joins: boolean;
  body_templates: boolean;
  event_feeds: boolean;
  command_templates: boolean;
};

export type RefusedOperationFact = {
  operation_id: string;
  method: string;
  path: string;
};

export type CapabilityRequestFacts = {
  vendorName: string;
  /** Vocabulary that refused, e.g. "v1". */
  vocabulary: string;
  /** Vocabulary to build with instead, e.g. "v2". */
  suggestedVocabulary: string;
  /** The deterministic refusal reason. */
  reason: string;
  refusedMethods: string[] | null;
  refusedProtocol: string | null;
  docType: string | null;
  authKind: string | null;
  operations: RefusedOperationFact[];
  recordTypes: string[];
  hasBodyFields: boolean | null;
  hasEventFeeds: boolean | null;
  /** Docs provenance label (docs_path / docs_url / "inline"). */
  docsSource: string;
  docsChars: number;
  docsText: string;
  /** Credential env-ref NAME only — never a value. */
  credentialRef: string;
  /** GET /capabilities versions, or null when the fetch failed. */
  capabilities: Record<string, CapabilityVersionFacts> | null;
  /** Refusal date, YYYY-MM-DD (passed in so the output is deterministic). */
  refusedDate: string;
};

const VERSION_ORDER = ["v1", "v2", "v3"] as const;

function emptyVersion(): CapabilityVersionFacts {
  return {
    methods: [],
    auth_types: [],
    pagination_strategies: [],
    protocols: [],
    joins: false,
    body_templates: false,
    event_feeds: false,
    command_templates: false,
  };
}

/**
 * What ConnectorSpec <vocabulary> can express. The API reports each
 * version as a delta ("v2 = everything in v1, plus"), so merge v1..vN —
 * the same reading the Knowledge page shows.
 */
export function cumulativeCapabilities(
  versions: Record<string, CapabilityVersionFacts> | null,
  vocabulary: string,
): CapabilityVersionFacts {
  const merged = emptyVersion();
  if (!versions) return merged;
  const upto = VERSION_ORDER.indexOf(vocabulary as (typeof VERSION_ORDER)[number]);
  const steps = upto < 0 ? [...VERSION_ORDER] : VERSION_ORDER.slice(0, upto + 1);
  for (const v of steps) {
    const ver = versions[v];
    if (!ver) continue;
    for (const k of ["methods", "auth_types", "pagination_strategies", "protocols"] as const) {
      for (const item of ver[k] ?? []) {
        if (!merged[k].includes(item)) merged[k].push(item);
      }
    }
    merged.joins = merged.joins || ver.joins === true;
    merged.body_templates = merged.body_templates || ver.body_templates === true;
    merged.event_feeds = merged.event_feeds || ver.event_feeds === true;
    merged.command_templates = merged.command_templates || ver.command_templates === true;
  }
  for (const k of ["methods", "auth_types", "pagination_strategies", "protocols"] as const) {
    merged[k].sort();
  }
  return merged;
}

export function capabilityRequestFilename(vendorName: string, vocabulary: string): string {
  const slug = vendorName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `capability-request-${slug || "vendor"}-${vocabulary}.md`;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function listOrDash(items: string[]): string {
  return items.length > 0 ? items.join(", ") : "—";
}

function yesNo(value: boolean): string {
  return value ? "yes" : "no";
}

/**
 * Assemble the capability-request Markdown deterministically from facts.
 * Same input → same file.
 */
export function buildCapabilityRequestMarkdown(facts: CapabilityRequestFacts): string {
  const vendor = facts.vendorName.trim() || "Unnamed vendor";
  const v = facts.vocabulary;
  const suggested = facts.suggestedVocabulary;
  const available = cumulativeCapabilities(facts.capabilities, v);

  const opMethods = [...new Set(facts.operations.map((o) => o.method).filter((m) => m))].sort();
  const neededMethods = facts.refusedMethods?.length
    ? [...facts.refusedMethods].sort()
    : opMethods.length
      ? opMethods
      : ["(none documented)"];
  const protocolNeeded = facts.refusedProtocol ?? "http";
  const authNeeded = facts.authKind ?? "not stated in these docs";
  const bodyNeeded = facts.hasBodyFields === true || neededMethods.includes("POST");
  const eventsNeeded = facts.hasEventFeeds === true || opMethods.includes("EVENT") || protocolNeeded !== "http";
  const commandsNeeded = protocolNeeded !== "http";

  const availableUnknown = facts.capabilities === null;
  const avail = (text: string) => (availableUnknown ? "unknown (capabilities fetch failed)" : text);

  const gap = (needed: string, availableText: string) => `| ${needed} | ${availableText} |`;

  const methodGap = neededMethods.some((m) => !available.methods.includes(m));
  const authGap = facts.authKind !== null && !available.auth_types.includes(facts.authKind);
  const protocolGap = !available.protocols.includes(protocolNeeded);
  const bodyGap = bodyNeeded && !available.body_templates;
  const eventsGap = eventsNeeded && !available.event_feeds;
  const commandsGap = commandsNeeded && !available.command_templates;

  const lines: string[] = [];
  lines.push(`### Capability request: ${vendor} docs cannot be expressed in ConnectorSpec ${v}`);
  lines.push("");
  lines.push("Status: DRAFT (assembled deterministically from a refused build; needs a language-version decision)");
  lines.push("");
  lines.push(`Summary: ${vendor} docs cannot be expressed in ConnectorSpec ${v}: ${facts.reason}`);
  lines.push("");
  lines.push("Context");
  lines.push("");
  lines.push(
    `- The build page's vocabulary check refused these docs on ${v} with 0 model calls: "${facts.reason}".`,
  );
  lines.push(
    `- Suggested next vocabulary: ${suggested} (one-click "Build with ${suggested}" on the refused build).`,
  );
  lines.push(
    `- Docs: ${facts.docsSource} (${facts.docsChars} chars, ${facts.docType ?? "unparsed kind"}); ` +
      `${facts.operations.length} operation(s), ${facts.recordTypes.length} record type(s); ` +
      `credential ${facts.credentialRef} (name only — values never leave the environment).`,
  );
  lines.push("");
  lines.push(`The gap (what the docs need vs what ${v} can express)`);
  lines.push("");
  lines.push(`| Capability | Needed by these docs | Available in ConnectorSpec ${v} |`);
  lines.push("| --- | --- | --- |");
  lines.push(gap(`Methods: ${listOrDash(neededMethods)}`, avail(listOrDash(available.methods))));
  lines.push(gap(`Auth: ${authNeeded}`, avail(listOrDash(available.auth_types))));
  lines.push(
    gap(
      "Pagination: see the operations below",
      avail(listOrDash(available.pagination_strategies)),
    ),
  );
  lines.push(gap(`Protocols: ${protocolNeeded}`, avail(listOrDash(available.protocols))));
  lines.push(gap("Joins: not stated in these docs", avail(yesNo(available.joins))));
  lines.push(gap(`Request bodies: ${bodyNeeded ? "yes (JSON body)" : "not stated in these docs"}`, avail(yesNo(available.body_templates))));
  lines.push(gap(`Event feeds: ${eventsNeeded ? "yes" : "not stated in these docs"}`, avail(yesNo(available.event_feeds))));
  lines.push(gap(`Command templates: ${commandsNeeded ? "yes (framed device)" : "not stated in these docs"}`, avail(yesNo(available.command_templates))));
  lines.push("");
  lines.push("What the new version must add");
  lines.push("");
  const additions: string[] = [];
  if (methodGap && !availableUnknown) {
    additions.push(
      `Accept ${neededMethods.filter((m) => !available.methods.includes(m)).join(", ")} operations` +
        (bodyNeeded ? " with a JSON body template" : "") +
        ` (${v} has: ${listOrDash(available.methods)}).`,
    );
  }
  if (authGap && !availableUnknown && facts.authKind) {
    additions.push(
      `Accept ${facts.authKind} credentials` +
        (facts.hasBodyFields ? " placed into named JSON body fields" : "") +
        ` (${v} has: ${listOrDash(available.auth_types)}).`,
    );
  }
  if (protocolGap && !availableUnknown) {
    additions.push(
      `Accept ${protocolNeeded} event feeds over the on-premise relay (${v} has: ${listOrDash(available.protocols)}).`,
    );
  }
  if (bodyGap && !availableUnknown && !methodGap) {
    additions.push(`Accept request bodies with a JSON body template (${v} has none).`);
  }
  if (eventsGap && !availableUnknown && !protocolGap) {
    additions.push(`Accept pushed event feeds (${v} has none).`);
  }
  if (commandsGap && !availableUnknown && !protocolGap) {
    additions.push(`Accept host-originated command templates for the framed device (${v} has none).`);
  }
  if (additions.length === 0) {
    additions.push(
      `Express what the refusal names: "${facts.reason}" — ` +
        (availableUnknown
          ? "re-fetch GET /capabilities and re-check the table above."
          : "no row above shows a gap, so restate the docs shape before cutting a version."),
    );
  }
  additions.forEach((item, i) => lines.push(`${i + 1}. ${item}`));
  lines.push("");
  lines.push("The vendor's spec");
  lines.push("");
  lines.push(`- Source: ${facts.docsSource}; size: ${facts.docsChars} chars.`);
  if (facts.operations.length > 0) {
    lines.push("- Operations / records the docs describe:");
    for (const op of facts.operations) {
      lines.push(`  - ${op.operation_id || "(unnamed)"} — ${op.method} ${op.path}`);
    }
  } else {
    lines.push("- Operations / records the docs describe: none parsed (see the appendix).");
  }
  if (facts.recordTypes.length > 0) {
    lines.push(`- Record types: ${facts.recordTypes.join(", ")}.`);
  }
  lines.push("");
  lines.push("Acceptance tests");
  lines.push("");
  lines.push(`1. The same docs build on ${suggested} and the first sync is Complete.`);
  lines.push(`2. Every connector on ${v} keeps a byte-identical spec signature (the REQ-020 regression rule).`);
  lines.push(`3. The refusal on ${v} stays (refuse safely).`);
  lines.push("4. The verifier's checks pass on the new release.");
  lines.push("5. A drift on the new connector heals on the ladder like any other.");
  lines.push("");
  lines.push("Appendix: the vendor's docs");
  lines.push("");
  lines.push(
    `<details><summary>Full docs text (${facts.docsChars} chars from ${facts.docsSource})</summary>`,
  );
  lines.push("");
  lines.push(`<pre>${escapeHtml(facts.docsText)}</pre>`);
  lines.push("");
  lines.push("</details>");
  lines.push("");
  lines.push("---");
  lines.push(`Drafted by AutoBridge from a refused build on ${facts.refusedDate}; no model was used.`);
  lines.push("");
  return lines.join("\n");
}
