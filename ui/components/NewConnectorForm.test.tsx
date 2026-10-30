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
  fetchMock.mockImplementation(async (url: unknown) => {
    const u = String(url);
    const e = entries.find((x) => u.includes(x.match));
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

    const [createUrl, createOpts] = findCall("/connectors");
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

    const [, createOpts] = findCall("/connectors");
    expect(JSON.parse(createOpts.body as string).vendor_docs_ref).toBe("simulators/docs/harbor.md");
    const [, buildOpts] = findCall("/connectors/conn-h/build");
    const buildBody = JSON.parse(buildOpts.body as string);
    expect(buildBody.docs_text).toBe(harborDocs);
    expect(buildBody.docs_url).toBeUndefined();
  });

  it("shows docs → draft → probes → result → release after the build starts", async () => {
    mockResponses([
      simsRoute(),
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

  it("names the documented totals check on the draft stage (REQ-049)", async () => {
    mockResponses([
      simsRoute(),
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
      expect(screen.getByTestId("build-stage-draft")).toHaveTextContent(
        "check added from the docs: totals must match meta.gross_sum",
      ),
    );
  });
});
