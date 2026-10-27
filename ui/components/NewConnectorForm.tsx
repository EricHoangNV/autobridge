"use client";

import { useState, useRef, type FormEvent } from "react";

const KNOWN_CREDENTIAL_REFS = ["ATLAS_API_KEY", "HARBOR_API_KEY", "ATLAS_BASE_URL", "HARBOR_BASE_URL"];
const ENTITY_OPTIONS = ["Reservation", "FolioCharge"] as const;
const BASE_URL_SUGGESTIONS = [
  "http://sim-atlas:8000",
  "http://sim-harbor:8000",
  "http://localhost:8102",
  "http://localhost:8103",
];

function getBaseUrl(): string {
  if (typeof window !== "undefined") {
    return process.env.NEXT_PUBLIC_API_URL ?? window.location.origin.replace(/:3000$/, ":8101");
  }
  return process.env.NEXT_PUBLIC_API_URL ?? process.env.CONTROL_URL ?? "http://localhost:8101";
}

export type CreateConnectorPayload = {
  name: string;
  base_url: string;
  credential_ref: string;
  vendor_docs_ref: string | null;
  target_entities: string[];
  runtime_target: Record<string, unknown>;
};

type SubmitState = "idle" | "creating" | "building" | "done" | "error";

export function NewConnectorForm() {
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Form fields
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [credentialRef, setCredentialRef] = useState("ATLAS_API_KEY");
  const [customCredential, setCustomCredential] = useState("");
  const [useCustomCredential, setUseCustomCredential] = useState(false);
  const [targetEntities, setTargetEntities] = useState<string[]>(["Reservation"]);
  const [docsUrl, setDocsUrl] = useState("");
  const [docsText, setDocsText] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);

  // UI state
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [result, setResult] = useState<{ connectorId: string; buildRunId?: string; jobId?: string } | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const effectiveCredentialRef = useCustomCredential ? customCredential.trim() : credentialRef;

  function validate(): boolean {
    const errs: Record<string, string> = {};
    if (!name.trim()) errs.name = "Name is required";
    if (!baseUrl.trim()) errs.base_url = "Sandbox base URL is required";
    else {
      try {
        new URL(baseUrl.trim());
      } catch {
        errs.base_url = "Must be a valid URL";
      }
    }
    if (!effectiveCredentialRef) errs.credential_ref = "Credential env-ref is required";
    if (targetEntities.length === 0) errs.target_entities = "Select at least one entity";
    const hasDocs = Boolean(docsUrl.trim() || docsText.trim() || fileContent);
    if (!hasDocs) errs.docs = "Provide docs via file upload, URL, or pasted text";
    setFieldErrors(errs);
    return Object.keys(errs).length === 0;
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) {
      setFileName(null);
      setFileContent(null);
      return;
    }
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      setFileContent(reader.result as string);
    };
    reader.onerror = () => {
      setFieldErrors((prev) => ({ ...prev, docs: "Failed to read file" }));
    };
    reader.readAsText(file);
  }

  function toggleEntity(entity: string) {
    setTargetEntities((prev) => (prev.includes(entity) ? prev.filter((e) => e !== entity) : [...prev, entity]));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setErrorMsg(null);
    setResult(null);
    if (!validate()) return;

    const connectorPayload: CreateConnectorPayload = {
      name: name.trim(),
      base_url: baseUrl.trim(),
      credential_ref: effectiveCredentialRef,
      vendor_docs_ref: docsUrl.trim() || (fileName ? `file:${fileName}` : null),
      target_entities: [...targetEntities],
      runtime_target: { kind: "local_process" },
    };

    setSubmitState("creating");

    const base = getBaseUrl().replace(/\/$/, "");

    try {
      // Step 1: Create connector
      const createRes = await fetch(`${base}/connectors`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(connectorPayload),
      });

      if (!createRes.ok) {
        const body = await createRes.text();
        throw new Error(`Create connector failed (${createRes.status}): ${body}`);
      }

      const created = (await createRes.json()) as { id: string };

      // Step 2: Start build
      setSubmitState("building");

      // Docs payload priority: fileContent > docsText > docsUrl
      const buildBody: Record<string, unknown> = {
        scenario_id: "baseline",
      };
      if (fileContent) {
        buildBody.docs_text = fileContent;
        buildBody.docs_path = fileName ?? undefined;
      } else if (docsText.trim()) {
        buildBody.docs_text = docsText.trim();
      } else if (docsUrl.trim()) {
        buildBody.docs_url = docsUrl.trim();
      }

      const buildRes = await fetch(`${base}/connectors/${encodeURIComponent(created.id)}/build`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildBody),
      });

      if (!buildRes.ok) {
        const body = await buildRes.text();
        // Connector was created; surface as partial success with error
        setResult({ connectorId: created.id });
        throw new Error(`Build start failed (${buildRes.status}): ${body} — connector ${created.id} was created`);
      }

      const buildResult = (await buildRes.json()) as { build_run_id?: string; job_id?: string };
      setResult({
        connectorId: created.id,
        buildRunId: buildResult.build_run_id,
        jobId: buildResult.job_id,
      });
      setSubmitState("done");
    } catch (err) {
      setSubmitState("error");
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  }

  const isBusy = submitState === "creating" || submitState === "building";

  return (
    <div data-testid="new-connector-form-wrap">
      <form data-testid="new-connector-form" onSubmit={handleSubmit} noValidate className="space-y-6">
        {/* Name */}
        <div>
          <label htmlFor="connector-name" className="block text-sm font-medium text-ink-2">
            Connector name <span className="text-stop">*</span>
          </label>
          <input
            id="connector-name"
            data-testid="input-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Atlas PMS — Staging"
            className="mt-1 w-full border border-rule px-3 py-2 text-sm shadow-sm focus:border-live focus:outline-none focus:ring-1 focus:ring-live"
            aria-invalid={Boolean(fieldErrors.name)}
            aria-describedby={fieldErrors.name ? "error-name" : undefined}
          />
          {fieldErrors.name ? (
            <p id="error-name" data-testid="error-name" className="mt-1 text-xs text-stop">
              {fieldErrors.name}
            </p>
          ) : null}
        </div>

        {/* Sandbox base URL */}
        <div>
          <label htmlFor="connector-base-url" className="block text-sm font-medium text-ink-2">
            Sandbox base URL <span className="text-stop">*</span>
          </label>
          <input
            id="connector-base-url"
            data-testid="input-base-url"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://sim-atlas:8000"
            list="base-url-suggestions"
            className="mt-1 w-full border border-rule px-3 py-2 text-sm shadow-sm focus:border-live focus:outline-none focus:ring-1 focus:ring-live"
            aria-invalid={Boolean(fieldErrors.base_url)}
            aria-describedby={fieldErrors.base_url ? "error-base-url" : undefined}
          />
          <datalist id="base-url-suggestions">
            {BASE_URL_SUGGESTIONS.map((u) => (
              <option key={u} value={u} />
            ))}
          </datalist>
          <p className="mt-1 text-xs text-ink-2">
            Points at the vendor sandbox. Suggestions: {BASE_URL_SUGGESTIONS.join(", ")}
          </p>
          {fieldErrors.base_url ? (
            <p id="error-base-url" data-testid="error-base-url" className="mt-1 text-xs text-stop">
              {fieldErrors.base_url}
            </p>
          ) : null}
        </div>

        {/* Credential env-ref picker */}
        <div>
          <label htmlFor="connector-credential-ref" className="block text-sm font-medium text-ink-2">
            Credential env-ref <span className="text-stop">*</span>
          </label>
          <p className="mt-1 text-xs text-ink-2">Env key name whose value holds the secret; the secret value is never stored in the spec.</p>
          <div className="mt-1 flex gap-2">
            <select
              id="connector-credential-ref"
              data-testid="input-credential-ref"
              value={useCustomCredential ? "__custom__" : credentialRef}
              onChange={(e) => {
                if (e.target.value === "__custom__") {
                  setUseCustomCredential(true);
                } else {
                  setUseCustomCredential(false);
                  setCredentialRef(e.target.value);
                }
              }}
              className="flex-1 border border-rule px-3 py-2 text-sm shadow-sm focus:border-live focus:outline-none focus:ring-1 focus:ring-live"
              aria-describedby={fieldErrors.credential_ref ? "error-credential-ref" : undefined}
            >
              {KNOWN_CREDENTIAL_REFS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
              <option value="__custom__">Custom…</option>
            </select>
            {useCustomCredential ? (
              <input
                data-testid="input-credential-custom"
                type="text"
                value={customCredential}
                onChange={(e) => setCustomCredential(e.target.value)}
                placeholder="e.g. MY_API_KEY"
                pattern="^[A-Z][A-Z0-9_]*$"
                className="flex-1 border border-rule px-3 py-2 text-sm shadow-sm focus:border-live focus:outline-none focus:ring-1 focus:ring-live"
                aria-label="Custom credential env-ref"
              />
            ) : null}
          </div>
          {fieldErrors.credential_ref ? (
            <p id="error-credential-ref" data-testid="error-credential-ref" className="mt-1 text-xs text-stop">
              {fieldErrors.credential_ref}
            </p>
          ) : null}
        </div>

        {/* Target entities */}
        <fieldset>
          <legend className="block text-sm font-medium text-ink-2">
            Target entities <span className="text-stop">*</span>
          </legend>
          <p className="mt-1 text-xs text-ink-2">Canonical entities this connector will sync.</p>
          <div className="mt-2 flex gap-4">
            {ENTITY_OPTIONS.map((entity) => (
              <label key={entity} className="inline-flex items-center gap-2 text-sm">
                <input
                  data-testid={`checkbox-entity-${entity}`}
                  type="checkbox"
                  checked={targetEntities.includes(entity)}
                  onChange={() => toggleEntity(entity)}
                  className="border-rule text-ink focus:ring-live"
                />
                {entity}
              </label>
            ))}
          </div>
          {fieldErrors.target_entities ? (
            <p data-testid="error-target-entities" className="mt-1 text-xs text-stop">
              {fieldErrors.target_entities}
            </p>
          ) : null}
        </fieldset>

        {/* Docs section */}
        <fieldset className="border border-rule p-4">
          <legend className="px-1 text-sm font-medium text-ink-2">Vendor docs</legend>
          <p className="text-xs text-ink-2">Provide at least one: file upload, URL, or pasted text. File content takes precedence.</p>

          {/* File upload */}
          <div className="mt-3">
            <label htmlFor="docs-file" className="block text-xs font-medium text-ink-2">
              Docs file upload
            </label>
            <div className="mt-1 flex items-center gap-2">
              <input
                id="docs-file"
                data-testid="input-docs-file"
                ref={fileInputRef}
                type="file"
                accept=".md,.txt,.json,.yaml,.yml,.html,.pdf"
                onChange={handleFileChange}
                className="block text-sm text-ink-2 file:mr-3 file:border file:border-rule file:bg-ground file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink"
              />
              {fileName ? (
                <span data-testid="file-name" className="text-xs text-ink-2">
                  {fileName} {fileContent ? `(${fileContent.length} chars)` : ""}
                </span>
              ) : null}
              {fileName ? (
                <button
                  type="button"
                  data-testid="clear-file"
                  onClick={() => {
                    setFileName(null);
                    setFileContent(null);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                  className="text-xs text-ink-2 hover:text-ink underline"
                >
                  Clear
                </button>
              ) : null}
            </div>
          </div>

          {/* Docs URL */}
          <div className="mt-3">
            <label htmlFor="docs-url" className="block text-xs font-medium text-ink-2">
              Docs URL
            </label>
            <input
              id="docs-url"
              data-testid="input-docs-url"
              type="url"
              value={docsUrl}
              onChange={(e) => setDocsUrl(e.target.value)}
              placeholder="https://docs.example.com/api"
              className="mt-1 w-full border border-rule px-3 py-2 text-sm shadow-sm focus:border-live focus:outline-none focus:ring-1 focus:ring-live"
            />
          </div>

          {/* Docs text */}
          <div className="mt-3">
            <label htmlFor="docs-text" className="block text-xs font-medium text-ink-2">
              Docs text (paste)
            </label>
            <textarea
              id="docs-text"
              data-testid="input-docs-text"
              value={docsText}
              onChange={(e) => setDocsText(e.target.value)}
              placeholder="Paste vendor API documentation here…"
              rows={6}
              className="mt-1 w-full border border-rule px-3 py-2 font-mono text-xs shadow-sm focus:border-live focus:outline-none focus:ring-1 focus:ring-live"
            />
            {docsText ? <p className="mt-1 text-xs text-ink-2">{docsText.length} characters</p> : null}
          </div>

          {fieldErrors.docs ? (
            <p data-testid="error-docs" className="mt-2 text-xs text-stop">
              {fieldErrors.docs}
            </p>
          ) : null}
        </fieldset>

        {/* Submit */}
        <div className="flex items-center gap-3">
          <button
            type="submit"
            data-testid="submit-build"
            disabled={isBusy}
            className="bg-live px-4 py-2 text-sm font-medium text-live-ink hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitState === "creating"
              ? "Creating connector…"
              : submitState === "building"
                ? "Starting build…"
                : "Create & start build"}
          </button>
          <a
            href="/connectors"
            data-testid="cancel-link"
            className="text-sm text-ink-2 hover:text-ink hover:underline"
          >
            Cancel
          </a>
          {isBusy ? (
            <span data-testid="busy-indicator" className="text-xs text-ink-2">
              Please wait…
            </span>
          ) : null}
        </div>
      </form>

      {/* Error */}
      {errorMsg ? (
        <div data-testid="submit-error" className="mt-4 border border-stop bg-band px-4 py-3 text-sm text-stop">
          {errorMsg}
          {result?.connectorId ? (
            <div className="mt-2">
              Connector <code className="bg-band px-1 py-0.5 text-xs">{result.connectorId}</code> was created.{" "}
              <a href={`/connectors/${result.connectorId}`} className="underline">
                View connector
              </a>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Success */}
      {submitState === "done" && result ? (
        <div data-testid="submit-success" className="mt-4 border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <p className="font-medium">Connector created and build started.</p>
          <p className="mt-1">
            Connector: <code className="bg-band px-1 py-0.5 text-xs">{result.connectorId}</code>
            {result.jobId ? (
              <>
                {" "}
                · Job <code className="bg-band px-1 py-0.5 text-xs">{result.jobId}</code>
              </>
            ) : null}
          </p>
          <div className="mt-2 flex gap-3">
            <a
              data-testid="success-view-connector"
              href={`/connectors/${result.connectorId}`}
              className="bg-emerald-900 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-800"
            >
              View connector
            </a>
            <a data-testid="success-view-connectors" href="/connectors" className="text-xs underline">
              Back to list
            </a>
          </div>
        </div>
      ) : null}
    </div>
  );
}
