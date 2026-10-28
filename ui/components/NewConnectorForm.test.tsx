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
    expect(fetchMock).not.toHaveBeenCalled();
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
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: "conn-new-123" }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ build_run_id: "build-1", job_id: "job-1" }),
      } as Response);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Atlas Staging");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "Vendor docs content here");

    fireEvent.click(screen.getByTestId("submit-build"));

    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [createUrl, createOpts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(createUrl).toContain("/connectors");
    const createBody = JSON.parse(createOpts.body as string);
    expect(createBody.name).toBe("Atlas Staging");
    expect(createBody.base_url).toBe("http://sim-atlas:8000");
    expect(createBody.credential_ref).toBe("ATLAS_API_KEY");
    expect(createBody.target_entities).toContain("Reservation");

    const [buildUrl, buildOpts] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(buildUrl).toContain("/connectors/conn-new-123/build");
    const buildBody = JSON.parse(buildOpts.body as string);
    expect(buildBody.docs_text).toBe("Vendor docs content here");

    expect(screen.getByTestId("success-view-connector")).toHaveAttribute("href", "/connectors/conn-new-123");
  });

  it("uses build docs_url when no text or file", async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "conn-2" }) } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ build_run_id: "b2", job_id: "j2" }) } as Response);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Harbor");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-harbor:8000");
    typeInto(screen.getByTestId("input-docs-url"), "https://docs.example.com/api");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const buildBody = JSON.parse((fetchMock.mock.calls[1] as [string, RequestInit])[1].body as string);
    expect(buildBody.docs_url).toBe("https://docs.example.com/api");
    expect(buildBody.docs_text).toBeUndefined();
  });

  it("shows error when create fails", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 422,
      text: async () => "invalid base_url",
    } as Response);

    render(<NewConnectorForm />);
    typeInto(screen.getByTestId("input-name"), "Bad");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-error")).toBeInTheDocument());
    expect(screen.getByTestId("submit-error").textContent).toContain("Create connector failed");
  });

  it("shows partial success when build fails after create", async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "conn-partial" }) } as Response)
      .mockResolvedValueOnce({ ok: false, status: 422, text: async () => "missing docs_text" } as Response);

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
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "conn-v1" }) } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ build_run_id: "b1", job_id: "j1" }) } as Response);

    render(<NewConnectorForm />);
    expect((screen.getByTestId("radio-vocabulary-v1") as HTMLInputElement).checked).toBe(true);
    typeInto(screen.getByTestId("input-name"), "Vocab Default");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const buildBody = JSON.parse((fetchMock.mock.calls[1] as [string, RequestInit])[1].body as string);
    expect(buildBody.spec_vocabulary).toBe("v1");
    expect(screen.getByTestId("submit-vocabulary").textContent).toContain("vocabulary v1");
  });

  it("sends spec_vocabulary v2 when selected", async () => {
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "conn-v2" }) } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => ({ build_run_id: "b2", job_id: "j2" }) } as Response);

    render(<NewConnectorForm />);
    fireEvent.click(screen.getByTestId("radio-vocabulary-v2"));
    typeInto(screen.getByTestId("input-name"), "Vocab V2");
    typeInto(screen.getByTestId("input-base-url"), "http://sim-atlas:8000");
    typeInto(screen.getByTestId("input-docs-text"), "docs");

    fireEvent.click(screen.getByTestId("submit-build"));
    await waitFor(() => expect(screen.getByTestId("submit-success")).toBeInTheDocument());

    const buildBody = JSON.parse((fetchMock.mock.calls[1] as [string, RequestInit])[1].body as string);
    expect(buildBody.spec_vocabulary).toBe("v2");
    expect(screen.getByTestId("submit-vocabulary").textContent).toContain("vocabulary v2");
  });
});
