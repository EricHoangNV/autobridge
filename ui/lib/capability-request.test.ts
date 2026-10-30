import { describe, it, expect } from "vitest";

import {
  buildCapabilityRequestMarkdown,
  capabilityRequestFilename,
  cumulativeCapabilities,
  type CapabilityRequestFacts,
  type CapabilityVersionFacts,
} from "./capability-request";

const V1: CapabilityVersionFacts = {
  methods: ["GET"],
  auth_types: ["api_key_header", "basic", "bearer"],
  pagination_strategies: ["cursor", "offset", "page"],
  protocols: ["http"],
  joins: false,
  body_templates: false,
  event_feeds: false,
  command_templates: false,
};

const V2: CapabilityVersionFacts = {
  methods: ["POST"],
  auth_types: ["body_fields"],
  pagination_strategies: ["body_cursor"],
  protocols: [],
  joins: true,
  body_templates: true,
  event_feeds: false,
  command_templates: false,
};

const V3: CapabilityVersionFacts = {
  methods: ["EVENT"],
  auth_types: ["edge_relay"],
  pagination_strategies: [],
  protocols: ["fias_tcp"],
  joins: false,
  body_templates: false,
  event_feeds: true,
  command_templates: true,
};

const VERSIONS = { v1: V1, v2: V2, v3: V3 };

function meridianFacts(): CapabilityRequestFacts {
  return {
    vendorName: "MeridianPMS",
    vocabulary: "v1",
    suggestedVocabulary: "v2",
    reason: "operations need POST with a JSON body and body-field auth; not expressible in ConnectorSpec v1",
    refusedMethods: ["POST"],
    refusedProtocol: null,
    docType: "openapi",
    authKind: "body_fields",
    operations: [
      { operation_id: "searchAvailability", method: "POST", path: "/search" },
      { operation_id: "createReservation", method: "POST", path: "/reservations" },
    ],
    recordTypes: ["Reservation", "Availability"],
    hasBodyFields: true,
    hasEventFeeds: false,
    docsSource: "simulators/docs/meridian-openapi.yaml",
    docsChars: 12345,
    docsText: "# Meridian docs <body> & more",
    credentialRef: "MERIDIAN_API_KEY",
    capabilities: VERSIONS,
    refusedDate: "2026-10-30",
  };
}

function pabxFacts(): CapabilityRequestFacts {
  return {
    vendorName: "FIAS PABX",
    vocabulary: "v2",
    suggestedVocabulary: "v3",
    reason: "event-driven fias_tcp record feeds are not expressible in ConnectorSpec v2",
    refusedMethods: null,
    refusedProtocol: "fias_tcp",
    docType: "markdown",
    authKind: "none",
    operations: [{ operation_id: "folio_charge", method: "EVENT", path: "folio charge feed" }],
    recordTypes: ["FolioCharge"],
    hasBodyFields: false,
    hasEventFeeds: true,
    docsSource: "simulators/docs/fias-pabx.md",
    docsChars: 987,
    docsText: "# FIAS PABX interface",
    credentialRef: "EDGE_TOKEN",
    capabilities: VERSIONS,
    refusedDate: "2026-10-30",
  };
}

describe("capability-request (REQ-058)", () => {
  it("meridian-on-v1 names the title, gap table, operations and acceptance tests", () => {
    const md = buildCapabilityRequestMarkdown(meridianFacts());
    expect(md).toContain("### Capability request: MeridianPMS docs cannot be expressed in ConnectorSpec v1");
    expect(md).toContain("Summary: MeridianPMS docs cannot be expressed in ConnectorSpec v1:");
    // Gap table: needed vs available in v1.
    expect(md).toContain("| Capability | Needed by these docs | Available in ConnectorSpec v1 |");
    expect(md).toContain("POST");
    expect(md).toContain("body_fields");
    // The vendor's spec: operations and record types.
    expect(md).toContain("searchAvailability — POST /search");
    expect(md).toContain("createReservation — POST /reservations");
    expect(md).toContain("Record types: Reservation, Availability.");
    // Numbered new-version items + the five generic acceptance tests.
    expect(md).toContain("What the new version must add");
    expect(md).toContain("Accept POST operations with a JSON body template");
    expect(md).toContain("1. The same docs build on v2 and the first sync is Complete.");
    expect(md).toContain("2. Every connector on v1 keeps a byte-identical spec signature (the REQ-020 regression rule).");
    expect(md).toContain("3. The refusal on v1 stays (refuse safely).");
    expect(md).toContain("4. The verifier's checks pass on the new release.");
    expect(md).toContain("5. A drift on the new connector heals on the ladder like any other.");
    // Appendix + footer.
    expect(md).toContain("simulators/docs/meridian-openapi.yaml");
    expect(md).toContain("Drafted by AutoBridge from a refused build on 2026-10-30; no model was used.");
  });

  it("pabx-on-v2 names the protocol gap and accumulates v1+v2 as available", () => {
    const md = buildCapabilityRequestMarkdown(pabxFacts());
    expect(md).toContain("### Capability request: FIAS PABX docs cannot be expressed in ConnectorSpec v2");
    // v2's available column merges v1 + v2 (GET from v1, POST from v2).
    expect(md).toContain("Available in ConnectorSpec v2");
    expect(md).toContain("GET, POST");
    expect(md).toContain("fias_tcp");
    expect(md).toContain("Accept fias_tcp event feeds over the on-premise relay");
    expect(md).toContain("1. The same docs build on v3 and the first sync is Complete.");
    expect(md).toContain("3. The refusal on v2 stays (refuse safely).");
  });

  it("includes the credential name only, never a value; docs text is HTML-escaped", () => {
    const md = buildCapabilityRequestMarkdown(meridianFacts());
    expect(md).toContain("credential MERIDIAN_API_KEY (name only");
    expect(md).not.toContain("sim-demo-key");
    expect(md).not.toContain("sk-");
    expect(md).toContain("&lt;body&gt; &amp; more");
    expect(md).not.toContain("# Meridian docs <body>");
  });

  it("is deterministic: same facts give the same file", () => {
    expect(buildCapabilityRequestMarkdown(meridianFacts())).toBe(buildCapabilityRequestMarkdown(meridianFacts()));
    expect(buildCapabilityRequestMarkdown(pabxFacts())).toBe(buildCapabilityRequestMarkdown(pabxFacts()));
  });

  it("still drafts when capabilities are unreachable, labelling the column", () => {
    const md = buildCapabilityRequestMarkdown({ ...meridianFacts(), capabilities: null });
    expect(md).toContain("unknown (capabilities fetch failed)");
    expect(md).toContain("re-fetch GET /capabilities and re-check the table above.");
    expect(md).toContain("### Capability request: MeridianPMS docs cannot be expressed in ConnectorSpec v1");
  });

  it("filenames slug the vendor and vocabulary", () => {
    expect(capabilityRequestFilename("MeridianPMS", "v1")).toBe("capability-request-meridianpms-v1.md");
    expect(capabilityRequestFilename("FIAS PABX", "v2")).toBe("capability-request-fias-pabx-v2.md");
    expect(capabilityRequestFilename("  ", "v1")).toBe("capability-request-vendor-v1.md");
  });

  it("cumulativeCapabilities merges v1..vN like the Knowledge page reads them", () => {
    const v1 = cumulativeCapabilities(VERSIONS, "v1");
    expect(v1.methods).toEqual(["GET"]);
    expect(v1.body_templates).toBe(false);
    const v2 = cumulativeCapabilities(VERSIONS, "v2");
    expect(v2.methods).toEqual(["GET", "POST"]);
    expect(v2.body_templates).toBe(true);
    const v3 = cumulativeCapabilities(VERSIONS, "v3");
    expect(v3.methods).toEqual(["EVENT", "GET", "POST"]);
    expect(v3.event_feeds).toBe(true);
  });
});
