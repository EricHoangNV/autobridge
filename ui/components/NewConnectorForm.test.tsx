import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import { NewConnectorForm } from "./NewConnectorForm";

function typeInto(el: HTMLElement, value: string) {
  fireEvent.change(el, { target: { value } });
}
function selectOption(el: HTMLSelectElement, value: string) {
  fireEvent.change(el, { target: { value } });
}

const fetchMock = vi.fn();

type RouteEntry = {
  match: string;
  ok?: boolean;
  status?: number;
  json?: unknown;
  text?: string;
};

/** URL-routing fetch stub: the form fetches /demo/simulators on mount, so
 * positional mockResolvedValueOnce queues shift. Routes match by URL
 * substring instead. */
function mockResponses(entries: RouteEntry[]) {
  // Longest match wins, so "/connectors/vocabulary-check" beats "/connectors".
  const ordered = [...entries].sort((a, b) => b.match.length - a.match.length);
  fetchMock.mockImplementation(async (url: unknown) => {
    const u = String(url);
    const e = ordered.find((x) => u.includes(x.match));
    if (!e) throw new Error(`unexpected fetch ${u}`);
    const ok = e.ok ?? true;
    return {
      ok,
      status: e.status ?? (ok ? 200 : 422),
      json: async () => e.json ?? {},
      text: async () => e.text ?? JSON.stringify(e.json ?? {}),
    } as Response;
  });
}

function simsRoute(rows: unknown[] = []) {
  return { match: "/demo/simulators", json: { simulators: rows } };
}

function checkOk(vocabulary = "v1") {
  return {
    match: "/connectors/vocabulary-check",
    json: { refused: false, vocabulary, suggested_vocabulary: null },
  };
}

/** The POST that creates the connector (not the vocabulary check, not a build). */
function findCreateCall(): [string, RequestInit] {
  const hit = fetchMock.mock.calls.find(
    ([url, init]) =>
      String(url).endsWith("/connectors") && (init as RequestInit)?.method !== "GET",
  );
  if (!hit) throw new Error(`no create-connector call: ${JSON.stringify(fetchMock.mock.calls.map((c) => c[0]))}`);
  return hit as [string, RequestInit];
}

function findCall(sub: string): [string, RequestInit] {
  const hit = fetchMock.mock.calls.find(([url]) => String(url).includes(sub));
  if (!hit) throw new Error(`no fetch call matching ${sub}: ${JSON.stringify(fetchMock.mock.calls.map((c) => c[0]))}`);
  return hit as [string, RequestInit];
}

describe("NewConnectorForm", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    // Provide global fetch mock
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders all required fields", () => {
    render(<NewConnectorForm />);
    expect(screen.getByTestId("new-connector-form")).toBeInTheDocument();
    expect(screen.getByTestId("input-name")).toBeInTheDocument();
    expect(screen.getByTestId("input-base-url")).toBeInTheDocument();
    expect(screen.getByTestId("input-credential-ref")).toBeInTheDocument();
    expect(screen.getByTestId("checkbox-entity-Reservation")).toBeInTheDocument();
    expect(screen.getByTestId("checkbox-entity-FolioCharge")).toBeInTheDocument();
    expect(screen.getByTestId("input-docs-file")).toBeInTheDocument();
    expect(screen.getByTestId("input-docs-url")).toBeInTheDocument();
    expect(screen.getByTestId("input-docs-text")).toBeInTheDocument();
    expect(screen.getByTestId("submit-build")).toBeInTheDocument();
    expect(screen.getByTestId("cancel-link")).toBeInTheDocument();
  });

  it("shows validation errors when submitting empty", async () => {
    render(<NewConnectorForm />);
    fireEvent.click(screen.getByTestId("submit-build"));
    expect(await screen.findByTestId("error-name")).toBeInTheDocument();
    expect(screen.getByTestId("error-base-url")).toBeInTheDocument();
    expect(screen.getByTestId("error-docs")).toBeInTheDocument();
    // The mount-time simulators fetch may fire; no connector/build write must.
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/connectors")),
    ).toHaveLength(0);
  });

  it("validates base URL format", async () => {
    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Test Connector");
    typeInto(screen.getByTestId("input-base-url"), "not-a-url");
    typeInto(screen.getByTestId("input-docs-text"), "some docs");
    fireEvent.click(screen.getByTestId("submit-build"));
    expect(await screen.findByTestId("error-base-url")).toHaveTextContent("valid URL");
  });

  it("validates target entities at least one", async () => {
    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Test");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");
    // Uncheck the default Reservation
    fireEvent.click(screen.getByTestId("checkbox-entity-Reservation"));
    fireEvent.click(screen.getByTestId("submit-build"));
    expect(await screen.findByTestId("error-target-entities")).toBeInTheDocument();
  });

  it("allows custom credential ref", () => {
    render(<NewConnectorForm />);
    const select = screen.getByTestId("input-credential-ref") as HTMLSelectElement;
    selectOption(select, "__custom__");
    expect(screen.getByTestId("input-credential-custom")).toBeInTheDocument();
    typeInto(screen.getByTestId("input-credential-custom"), "MY_KEY");
    expect(screen.getByTestId("input-credential-custom")).toHaveValue("MY_KEY");
  });

  it("toggles target entities", () => {
    render(<NewConnectorForm />);
    const folioCharge = screen.getByTestId("checkbox-entity-FolioCharge") as HTMLInputElement;
    expect(folioCharge.checked).toBe(false);
    fireEvent.click(folioCharge);
    expect(folioCharge.checked).toBe(true);
    fireEvent.click(screen.getByTestId("checkbox-entity-Reservation"));
    const reservation = screen.getByTestId("checkbox-entity-Reservation") as HTMLInputElement;
    expect(reservation.checked).toBe(false);
  });

  it("creates connector then starts build on submit", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-new-123/build", json: { build_run_id: "build-1", job_id: "job-1" } },
      { match: "/connectors", json: { id: "conn-new-123" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      {
        match: "/runs/",
        json: {
          kind: "build",
          run: {
            inputs: { source: "docs" },
            draft_spec: { spec_version: 1, operations: { list_reservations: {} } },
            ladder_trace: [{ stage: "probes" }],
            result: { success: true, verified: true, release_id: "rel-1" },
            model_usage: {},
          },
        },
      },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Atlas Staging");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "Vendor docs content here");

    fireEvent.click(screen.getByTestId("submit-build"));

    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const [createUrl, createOpts] = findCreateCall();
    expect(createUrl).toContain("/connectors");
    const createBody = JSON.parse(createOpts.body as string);
    expect(createBody.name).toBe("Atlas Staging");
    expect(createBody.base_url).toBe("http://sim-atlas:8000");
    expect(createBody.credential_ref).toBe("ATLAS_API_KEY");
    expect(createBody.target_entities).toContain("Reservation");

    const [buildUrl, buildOpts] = findCall("/connectors/conn-new-123/build");
    expect(buildUrl).toContain("/connectors/conn-new-123/build");
    const buildBody = JSON.parse(buildOpts.body as string);
    expect(buildBody.docs_text).toBe("Vendor docs content here");

    expect(screen.getByTestId("success-view-connector")).toHaveAttribute("href", "/connectors/conn-new-123");
  });

  it("uses build docs_url when no text or file", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-2/build", json: { build_run_id: "b2", job_id: "j2" } },
      { match: "/connectors", json: { id: "conn-2" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      { match: "/runs/", json: { kind: "build", run: { result: { success: true } } } },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Harbor");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-harbor:8000");
    typeInto(screen.getByTestId("input-docs-url"), "https://docs.example.com/api");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const buildBody = JSON.parse(findCall("/connectors/conn-2/build")[1].body as string);
    expect(buildBody.docs_url).toBe("https://docs.example.com/api");
    expect(buildBody.docs_text).toBeUndefined();
  });

  it("shows error when create fails", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors", ok: false, status: 422, text: "invalid base_url" },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Bad");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-error")).toBeInTheDocument());
    expect(screen.getByTestId("submit-error").textContent).toContain("Create connector failed");
  });

  it("shows partial success when build fails after create", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-partial/build", ok: false, status: 422, text: "missing docs_text" },
      { match: "/connectors", json: { id: "conn-partial" } },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Partial");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-error")).toBeInTheDocument());
    expect(screen.getByTestId("submit-error").textContent).toContain("conn-partial");
  });

  it("handles file upload and clear", async () => {
    render(<NewConnectorForm />);
    const fileInput = screen.getByTestId("input-docs-file") as HTMLInputElement;
    const file = new File(["# API docs content"], "docs.md", { type: "text/markdown" });

    fireEvent.change(fileInput, { target: { files: [file] } });
    // FileReader is async; wait for state
    await waitFor(() => expect(screen.getByTestId("file-name")).toBeInTheDocument());
    expect(screen.getByTestId("file-name").textContent).toContain("docs.md");

    fireEvent.click(screen.getByTestId("clear-file"));
    expect(screen.queryByTestId("file-name")).not.toBeInTheDocument();
  });

  it("has link back to connectors and cancel", () => {
    render(<NewConnectorForm />);
    expect(screen.getByTestId("cancel-link")).toHaveAttribute("href", "/connectors");
  });

  it("pins spec_vocabulary v1 by default and shows it on success", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-v1/build", json: { build_run_id: "b1", job_id: "j1" } },
      { match: "/connectors", json: { id: "conn-v1" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      { match: "/runs/", json: { kind: "build", run: { result: { success: true } } } },
    ]);

    render(<NewConnectorForm />);
    expect((screen.getByTestId("radio-vocabulary-v1") as HTMLInputElement).checked).toBe(true);
    typeInto(screen.getByTestId("input-name"), "Vocab Default");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const buildBody = JSON.parse(findCall("/connectors/conn-v1/build")[1].body as string);
    expect(buildBody.spec_vocabulary).toBe("v1");
    expect(screen.getByTestId("submit-vocabulary").textContent).toContain("vocabulary v1");
  });

  it("sends spec_vocabulary v2 when selected", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-v2/build", json: { build_run_id: "b2", job_id: "j2" } },
      { match: "/connectors", json: { id: "conn-v2" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      { match: "/runs/", json: { kind: "build", run: { result: { success: true } } } },
    ]);

    render(<NewConnectorForm />);
    fireEvent.click(screen.getByTestId("radio-vocabulary-v2"));
    typeInto(screen.getByTestId("input-name"), "Vocab V2");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const buildBody = JSON.parse(findCall("/connectors/conn-v2/build")[1].body as string);
    expect(buildBody.spec_vocabulary).toBe("v2");
    expect(screen.getByTestId("submit-vocabulary").textContent).toContain("vocabulary v2");
  });

  it("loads Harbor docs from the simulator picker and sends docs_text", async () => {
    const harborDocs = "# HarborPMS API (messy wiki)";
    mockResponses([
      checkOk(),
      {
        match: "/demo/simulators",
        json: {
          simulators: [
            {
              id: "sim-harbor",
              display_name: "HarborPMS",
              docs_source: "simulators/docs/harbor.md",
              developers_url: "http://localhost:8106/developers",
            },
          ],
        },
      },
      {
        match: "/demo/docs",
        json: {
          target: "sim-harbor",
          display_name: "HarborPMS",
          docs_source: "simulators/docs/harbor.md",
          characters: harborDocs.length,
          docs_text: harborDocs,
        },
      },
      { match: "/connectors/conn-h/build", json: { build_run_id: "bh", job_id: "jh" } },
      { match: "/connectors", json: { id: "conn-h" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      {
        match: "/runs/",
        json: {
          kind: "build",
          run: {
            inputs: { source: "simulators/docs/harbor.md" },
            draft_spec: { spec_version: 1, operations: { list_reservations: {}, list_folios: {} } },
            ladder_trace: [{ stage: "classify" }, { stage: "probes" }],
            result: { success: true, verified: true, release_id: "rel-h" },
            model_usage: {},
          },
        },
      },
    ]);

    render(<NewConnectorForm />);
    await waitFor(() => expect(screen.getByTestId("input-sim-docs")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("input-sim-docs"), { target: { value: "sim-harbor" } });

    await waitFor(() => expect(screen.getByTestId("sim-docs-info")).toBeInTheDocument());
    expect(screen.getByTestId("sim-docs-info").textContent).toContain("HarborPMS developers portal");
    expect((screen.getByTestId("input-docs-text") as HTMLTextAreaElement).value).toBe(harborDocs);
    // Sandbox base URL suggested from the simulator's developers URL.
    expect((screen.getByTestId("input-base-url") as HTMLInputElement).value).toBe("http://localhost:8106");

    typeInto(screen.getByTestId("input-name"), "HarborPMS");
    selectOption(screen.getByTestId("input-credential-ref"), "HARBOR_API_KEY");
    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const [, createOpts] = findCreateCall();
    expect(JSON.parse(createOpts.body as string).vendor_docs_ref).toBe("simulators/docs/harbor.md");
    const [, buildOpts] = findCall("/connectors/conn-h/build");
    const buildBody = JSON.parse(buildOpts.body as string);
    expect(buildBody.docs_text).toBe(harborDocs);
    expect(buildBody.docs_url).toBeUndefined();
  });

  it("shows docs → draft → probes → result → release after the build starts", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-p/build", json: { build_run_id: "bp", job_id: "jp" } },
      { match: "/connectors", json: { id: "conn-p" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      {
        match: "/runs/",
        json: {
          kind: "build",
          run: {
            inputs: { source: "docs" },
            draft_spec: { spec_version: 1, operations: { list_reservations: {} } },
            ladder_trace: [{ stage: "classify" }, { stage: "probes" }, { stage: "verify" }],
            result: { success: true, verified: true, release_id: "rel-p" },
            model_usage: {},
          },
        },
      },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Progress");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");
    fireEvent.click(screen.getByTestId("submit-build"));

    await waitFor(() => expect(screen.getByTestId("build-progress")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("build-stage-docs")).toBeInTheDocument());
    expect(screen.getByTestId("build-stage-docs").textContent).toContain("Docs read");
    expect(screen.getByTestId("build-stage-draft").textContent).toContain("spec v1");
    expect(screen.getByTestId("build-stage-probes").textContent).toContain("3 trace stages");
    expect(screen.getByTestId("build-stage-result").textContent).toContain("verified");
    expect(screen.getByTestId("build-stage-release").textContent).toContain("release rel-p");
    expect(screen.getByTestId("build-release-link")).toHaveAttribute("href", "/connectors/conn-p");
  });

  it("offers spec_vocabulary v3 for framed docs", () => {
    render(<NewConnectorForm />);
    expect(screen.getByTestId("radio-vocabulary-v3")).toBeInTheDocument();
  });

  it("names the documented totals check on its own stage (REQ-049, REQ-053 item 5)", async () => {    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-p/build", json: { build_run_id: "bp", job_id: "jp" } },
      { match: "/connectors", json: { id: "conn-p" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      {
        match: "/runs/",
        json: {
          kind: "build",
          run: {
            inputs: { source: "docs" },
            draft_spec: { spec_version: 1, operations: { list_reservations: {} } },
            ladder_trace: [
              { stage: "classify" },
              { stage: "page_total_check", detail: "check added from the docs: totals must match meta.gross_sum" },
              { stage: "verify" },
            ],
            result: { success: true, verified: true, release_id: "rel-p" },
            model_usage: {},
          },
        },
      },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Progress");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-harbor:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");
    fireEvent.click(screen.getByTestId("submit-build"));

    await waitFor(() => expect(screen.getByTestId("build-progress")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId("build-stage-docscheck")).toHaveTextContent(
        "check added from the docs: totals must match meta.gross_sum",
      ),
    );
  });

  it("sends no runtime choice; the engine's decision reads cloud for HTTP docs (REQ-050)", async () => {
    mockResponses([
      simsRoute(),
      checkOk(),
      { match: "/connectors/conn-rt/build", json: { build_run_id: "brt", job_id: "jrt" } },
      { match: "/connectors", json: { id: "conn-rt" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      {
        match: "/runs/",
        json: {
          kind: "build",
          run: {
            inputs: { source: "docs" },
            draft_spec: {
              spec_version: 2,
              connector: { id: "meridianpms", vendor: "meridianpms" },
              source: { base_url: "http://sim-meridian:8000" },
              operations: { list_orders: { method: "POST" } },
            },
            ladder_trace: [{ stage: "classify" }],
            result: { success: true, verified: true, release_id: "rel-rt" },
            model_usage: {},
          },
        },
      },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "MeridianPMS");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-meridian:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");
    fireEvent.click(screen.getByTestId("submit-build"));

    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const createBody = JSON.parse(findCreateCall()[1].body as string);
    expect(createBody).not.toHaveProperty("runtime_target");
    expect(screen.getByTestId("build-runtime-decision").textContent).toContain("runs: cloud");
  });

  it("shows each vocabulary version's release note (REQ-050)", () => {
    render(<NewConnectorForm />);
    expect(screen.getByTestId("radio-vocabulary-v1").parentElement?.textContent).toContain(
      "REST reads: GET with headers or basic auth",
    );
    expect(screen.getByTestId("radio-vocabulary-v2").parentElement?.textContent).toContain(
      "request bodies and joins (POST-style APIs)",
    );
    expect(screen.getByTestId("radio-vocabulary-v3").parentElement?.textContent).toContain(
      "event streams over TCP (on-premise devices)",
    );
  });

  it("builds the PABX live from its spec: no base-URL typing, no runtime choice, edge decision (REQ-050)", async () => {
    const fiasDocs = "# FIAS PABX interface";
    mockResponses([
      checkOk(),
      {
        match: "/demo/simulators",
        json: {
          simulators: [
            {
              id: "sim-fias-pabx",
              display_name: "FIAS PABX",
              docs_source: "simulators/docs/fias-pabx.md",
              developers_url: "http://localhost:8118/developers",
            },
          ],
        },
      },
      {
        match: "/demo/docs",
        json: {
          target: "sim-fias-pabx",
          display_name: "FIAS PABX",
          docs_source: "simulators/docs/fias-pabx.md",
          characters: fiasDocs.length,
          docs_text: fiasDocs,
        },
      },
      { match: "/connectors/conn-fias/build", json: { build_run_id: "bf", job_id: "jf" } },
      { match: "/connectors", json: { id: "conn-fias" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      {
        match: "/runs/",
        json: {
          kind: "build",
          run: {
            inputs: { source: "simulators/docs/fias-pabx.md" },
            draft_spec: {
              spec_version: 3,
              connector: { id: "fiaspabx", vendor: "PABX edge", runtime_target: "edge" },
              source: { base_url: "fias://listen:8000", protocol: "fias_tcp" },
              operations: {},
            },
            ladder_trace: [{ stage: "classify" }],
            result: { success: true, verified: true, release_id: "rel-f" },
            model_usage: {},
          },
        },
      },
    ]);

    render(<NewConnectorForm />);
    await waitFor(() => expect(screen.getByTestId("input-sim-docs")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("input-sim-docs"), { target: { value: "sim-fias-pabx" } });

    await waitFor(() => expect(screen.getByTestId("edge-runtime-note")).toBeInTheDocument());
    // No base-URL typing for the PABX on camera.
    expect(screen.queryByTestId("input-base-url")).not.toBeInTheDocument();
    // Event docs need v3: the picker moves off the v1 default.
    expect((screen.getByTestId("radio-vocabulary-v3") as HTMLInputElement).checked).toBe(true);

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const createBody = JSON.parse(findCreateCall()[1].body as string);
    expect(createBody.name).toBe("PABX edge");
    expect(createBody.base_url).toBe("edge-relay://queue/fias-commands");
    expect(createBody).not.toHaveProperty("runtime_target");
    const buildBody = JSON.parse(findCall("/connectors/conn-fias/build")[1].body as string);
    expect(buildBody.docs_text).toBe(fiasDocs);
    expect(buildBody.spec_vocabulary).toBe("v3");
    await waitFor(() =>
      expect(screen.getByTestId("build-runtime-decision").textContent).toContain(
        "runs: on-premise edge agent, via the relay",
      ),
    );
  });

  it("refuses by vocabulary before creating a connector (REQ-053 item 7)", async () => {
    const meridianDocs = "# Meridian POST API";
    mockResponses([
      simsRoute(),
      {
        match: "/connectors/vocabulary-check",
        json: { refused: true, reason: "operations need POST with a JSON body and body-field auth; not expressible in ConnectorSpec v1", vocabulary: "v1", suggested_vocabulary: "v2" },
      },
      { match: "/connectors/conn-m2/build", json: { build_run_id: "bm2", job_id: "jm2" } },
      { match: "/connectors", json: { id: "conn-m2" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      { match: "/runs/", json: { kind: "build", run: { result: { success: true } } } },
    ]);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "meridianpms");
    typeInto(screen.getByTestId("input-base-url"), "http://localhost:8116");
    typeInto(screen.getByTestId("input-docs-text"), meridianDocs);

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("vocabulary-refused")).toBeInTheDocument());
    expect(screen.getByTestId("vocabulary-refused").textContent).toContain(
      "Refused by vocabulary v1",
    );
    expect(screen.getByTestId("vocabulary-refused").textContent).toContain("Build with v2");
    // No connector was created.
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/connectors"))).toHaveLength(0);
  });

  it("applies the simulator's credential and entities when its docs are picked (REQ-053 items 8/9)", async () => {
    const harborDocs = "# HarborPMS API (messy wiki)";
    mockResponses([
      {
        match: "/demo/simulators",
        json: {
          simulators: [
            {
              id: "sim-harbor",
              display_name: "HarborPMS",
              docs_source: "simulators/docs/harbor.md",
              developers_url: "http://localhost:8106/developers",
              suggested_credential_ref: "HARBOR_API_KEY",
              suggested_target_entities: ["Reservation", "FolioCharge"],
            },
          ],
        },
      },
      {
        match: "/demo/docs",
        json: {
          target: "sim-harbor",
          display_name: "HarborPMS",
          docs_source: "simulators/docs/harbor.md",
          characters: harborDocs.length,
          docs_text: harborDocs,
        },
      },
      checkOk(),
      { match: "/connectors/conn-h2/build", json: { build_run_id: "bh", job_id: "jh" } },
      { match: "/connectors", json: { id: "conn-h2" } },
      { match: "/jobs/", json: { state: "succeeded" } },
      { match: "/runs/", json: { kind: "build", run: { result: { success: true } } } },
    ]);

    render(<NewConnectorForm />);
    await waitFor(() => expect(screen.getByTestId("input-sim-docs")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("input-sim-docs"), { target: { value: "sim-harbor" } });
    await waitFor(() => expect(screen.getByTestId("sim-docs-info")).toBeInTheDocument());

    // Credential + entities follow the simulator, not the form defaults.
    expect((screen.getByTestId("input-credential-ref") as HTMLSelectElement).value).toBe("HARBOR_API_KEY");
    expect((screen.getByTestId("checkbox-entity-FolioCharge") as HTMLInputElement).checked).toBe(true);

    typeInto(screen.getByTestId("input-name"), "HarborPMS");
    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const createBody = JSON.parse(findCreateCall()[1].body as string);
    expect(createBody.credential_ref).toBe("HARBOR_API_KEY");
    expect(createBody.target_entities).toEqual(["Reservation", "FolioCharge"]);
    const buildBody = JSON.parse(findCall("/connectors/conn-h2/build")[1].body as string);
    expect(buildBody.probe_wire_shape).toBe(true);
  });
});
