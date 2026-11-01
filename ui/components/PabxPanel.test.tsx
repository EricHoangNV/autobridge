import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";

import { PabxPanel } from "./PabxPanel";

function renderPanel(connectorId: string, connectorName: string) {
  // Fresh SWR cache per test: cached /edge/agents from one test must
  // never decide the agent match of the next.
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <PabxPanel connectorId={connectorId} connectorName={connectorName} />
    </SWRConfig>,
  );
}

const fetchMock = vi.fn();

function mockResponses(entries: { match: string; json: unknown }[]) {
  const ordered = [...entries].sort((a, b) => b.match.length - a.match.length);
  fetchMock.mockImplementation(async (url: unknown) => {
    const u = String(url);
    const e = ordered.find((x) => u.includes(x.match));
    if (!e) throw new Error(`unexpected fetch ${u}`);
    return { ok: true, status: 200, json: async () => e.json, text: async () => "" } as Response;
  });
}

const AGENTS = {
  agents: [{ agent_id: "conn-1", link_up: true, link_detail: "up", updated_at: new Date().toISOString() }],
};

const OWN_POSTINGS = {
  postings: [
    { idempotency_key: "P#000001", agent_id: "conn-1", record_type: "PS", received_at: new Date().toISOString(), occurred_at: null },
    { idempotency_key: "P#000002", agent_id: "conn-1", record_type: "PS", received_at: new Date().toISOString(), occurred_at: null },
  ],
};

const GLOBAL_POSTINGS = {
  postings: [
    ...OWN_POSTINGS.postings,
    { idempotency_key: "P#000003", agent_id: "other", record_type: "PS", received_at: "2020-01-01T00:00:00Z", occurred_at: null },
    { idempotency_key: "P#000004", agent_id: "other", record_type: "PS", received_at: "2020-01-01T00:00:00Z", occurred_at: null },
    { idempotency_key: "P#000005", agent_id: "other", record_type: "PS", received_at: "2020-01-01T00:00:00Z", occurred_at: null },
  ],
};

describe("PabxPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("filters postings by this connector's agent (REQ-067 item 8)", async () => {
    mockResponses([
      { match: "/demo/pabx-events", json: [] },
      { match: "agent_id=conn-1", json: OWN_POSTINGS },
      { match: "/edge/agents", json: AGENTS },
      { match: "/edge/postings", json: GLOBAL_POSTINGS },
    ]);
    renderPanel("conn-1", "PABX edge");
    await waitFor(() => expect(screen.getByTestId("pabx-postings")).toHaveTextContent("2 received"));
    // The relay-global queue (5 records, stale timestamp) never shows.
    expect(screen.getByTestId("pabx-postings").textContent).not.toContain("5 received");
    const filtered = fetchMock.mock.calls.some(([url]) => String(url).includes("agent_id=conn-1"));
    expect(filtered).toBe(true);
  });

  it("stays unfiltered until the agent is known (REQ-067 item 8)", async () => {
    mockResponses([
      { match: "/demo/pabx-events", json: [] },
      { match: "/edge/agents", json: { agents: [] } },
      { match: "/edge/postings", json: GLOBAL_POSTINGS },
    ]);
    renderPanel("conn-1", "PABX edge");
    await waitFor(() => expect(screen.getByTestId("pabx-postings")).toHaveTextContent("5 received"));
  });

  it("shows plain link state, never raw debug text (REQ-068 item 7)", async () => {
    mockResponses([
      { match: "/demo/pabx-events", json: [] },
      { match: "agent_id=conn-1", json: OWN_POSTINGS },
      {
        match: "/edge/agents",
        json: { agents: [{ agent_id: "conn-1", link_up: true, link_detail: 'accepted=2 dropped=0 layouts={"DR": []}', updated_at: new Date().toISOString() }] },
      },
      { match: "/edge/postings", json: GLOBAL_POSTINGS },
    ]);
    const { unmount } = renderPanel("conn-1", "PABX edge");
    await waitFor(() => expect(screen.getByTestId("pabx-link-state")).toHaveTextContent("up"));
    expect(screen.getByTestId("pabx-link-state").textContent).not.toContain("accepted=");
    expect(screen.getByTestId("pabx-link-state").textContent).not.toContain("layouts=");
    unmount();
  });

  it("reads re-handshaking while a live agent's link is down (REQ-068 item 7)", async () => {
    mockResponses([
      { match: "/demo/pabx-events", json: [] },
      { match: "agent_id=conn-1", json: OWN_POSTINGS },
      {
        match: "/edge/agents",
        json: { agents: [{ agent_id: "conn-1", link_up: false, link_detail: "accepted=1 dropped=1", updated_at: new Date().toISOString() }] },
      },
      { match: "/edge/postings", json: GLOBAL_POSTINGS },
    ]);
    renderPanel("conn-1", "PABX edge");
    await waitFor(() => expect(screen.getByTestId("pabx-link-state")).toHaveTextContent("re-handshaking"));
  });

  it("falls back to the single PABX agent for postings (REQ-068 item 7)", async () => {
    // The edge agent id never equals the connector id — the panel must
    // still filter to the known agent instead of the global queue.
    mockResponses([
      { match: "/demo/pabx-events", json: [] },
      { match: "agent_id=grand-plaza-pabx", json: OWN_POSTINGS },
      {
        match: "/edge/agents",
        json: { agents: [{ agent_id: "grand-plaza-pabx", link_up: true, link_detail: "up", updated_at: new Date().toISOString() }] },
      },
      { match: "/edge/postings", json: GLOBAL_POSTINGS },
    ]);
    renderPanel("conn-other-id", "PABX edge");
    await waitFor(() => expect(screen.getByTestId("pabx-postings")).toHaveTextContent("2 received"));
    expect(screen.getByTestId("pabx-postings").textContent).not.toContain("5 received");
  });

  it("shows the record layout readably, never raw JSON (REQ-069 item 6)", async () => {
    mockResponses([
      { match: "/demo/pabx-events", json: [] },
      { match: "agent_id=conn-1", json: OWN_POSTINGS },
      {
        match: "/edge/agents",
        json: { agents: [{ agent_id: "conn-1", link_up: true, link_detail: 'up layouts={"DR": ["DA", "TI"], "PS": ["PA"]}', updated_at: new Date().toISOString() }] },
      },
      { match: "/edge/postings", json: GLOBAL_POSTINGS },
    ]);
    renderPanel("conn-1", "PABX edge");
    await waitFor(() => expect(screen.getByTestId("pabx-layout")).toHaveTextContent("DR: DA, TI"));
    expect(screen.getByTestId("pabx-layout")).toHaveTextContent("PS: PA");
    expect(screen.getByTestId("pabx-layout").textContent).not.toContain('{"DR"');
  });
});
