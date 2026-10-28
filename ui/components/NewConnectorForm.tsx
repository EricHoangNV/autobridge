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

// Same-origin /api base (REQ-018): the rewrite + middleware forward to control.
import { apiBaseUrl as getBaseUrl } from "@/lib/api/base";

export type CreateConnectorPayload = {
  name: string;
  base_url: string;
  credential_ref: string;
  vendor_docs_ref: string | null;
  target_entities: string[];
  runtime_target: Record<string, unknown>;
};

type SubmitState = "idle" | "creating" | "building" | "done" | "error";

const FIELD_LABEL = "block text-[15px] font-bold text-ink";
const FIELD_HINT = "mt-1.5 block text-sm text-ink-2";
const TEXT_INPUT =
  "mt-2 w-full border-[1.5px] border-off bg-ground px-3.5 py-3 font-mono text-base text-ink placeholder:text-ink-2/60 focus:border-live focus:outline-none";
const FIELD_ERROR = "mt-1.5 text-sm text-stop";

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
  const [vocabulary, setVocabulary] = useState<"v1" | "v2">("v1");

  // UI state
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [result, setResult] = useState<{
    connectorId: string;
    buildRunId?: string;
    jobId?: string;
    vocabulary: "v1" | "v2";
  } | null>(null);
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
        spec_vocabulary: vocabulary,
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
        setResult({ connectorId: created.id, vocabulary });
        throw new Error(`Build start failed (${buildRes.status}): ${body} — connector ${created.id} was created`);
      }

      const buildResult = (await buildRes.json()) as { build_run_id?: string; job_id?: string };
      setResult({
        connectorId: created.id,
        buildRunId: buildResult.build_run_id,
        jobId: buildResult.job_id,
        vocabulary,
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
      <form
        id="build-connector-form"
        data-testid="new-connector-form"
        onSubmit={handleSubmit}
        noValidate
        className="space-y-7"
      >
        {/* Vendor name */}
        <div>
          <label htmlFor="connector-name" className={FIELD_LABEL}>
            Vendor name <span className="text-live" aria-hidden="true">*</span>
          </label>
          <input
            id="connector-name"
            data-testid="input-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. LegacyStay"
            autoComplete="off"
            className={TEXT_INPUT}
            aria-invalid={Boolean(fieldErrors.name)}
            aria-describedby={fieldErrors.name ? "error-name" : undefined}
          />
          {fieldErrors.name ? (
            <p id="error-name" data-testid="error-name" className={FIELD_ERROR}>
              {fieldErrors.name}
            </p>
          ) : null}
        </div>

        {/* Docs */}
        <div>
          <label htmlFor="docs-url" className={FIELD_LABEL}>
            Docs <span className="text-live" aria-hidden="true">*</span>
          </label>
          <div className="mt-2 grid grid-cols-1 gap-2.5">
            <div className="flex items-center gap-2.5">
              <input
                id="docs-url"
                data-testid="input-docs-url"
                type="url"
                value={docsUrl}
                onChange={(e) => setDocsUrl(e.target.value)}
                placeholder="http://docs.vendor.local/api.md"
                className={TEXT_INPUT + " mt-0 flex-1"}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="btn quiet shrink-0 !px-4 !py-3 !text-[15px]"
              >
                Upload a file
              </button>
            </div>
            <input
              id="docs-file"
              data-testid="input-docs-file"
              ref={fileInputRef}
              type="file"
              accept=".md,.txt,.json,.yaml,.yml,.html,.pdf"
              onChange={handleFileChange}
              className="sr-only"
              aria-label="Docs file upload"
            />
            {fileName ? (
              <p className="flex items-center gap-3 font-mono text-sm text-ink">
                <span data-testid="file-name">
                  {fileName} {fileContent ? `(${fileContent.length} chars)` : ""}
                </span>
                <button
                  type="button"
                  data-testid="clear-file"
                  onClick={() => {
                    setFileName(null);
                    setFileContent(null);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                  className="link !text-[13px]"
                >
                  Clear
                </button>
              </p>
            ) : null}
            <textarea
              id="docs-text"
              data-testid="input-docs-text"
              value={docsText}
              onChange={(e) => setDocsText(e.target.value)}
              placeholder="Or paste the vendor API documentation here…"
              rows={5}
              className={TEXT_INPUT + " mt-0 text-sm"}
            />
          </div>
          <span className={FIELD_HINT}>Markdown, or OpenAPI 3 as YAML or JSON. Docs can be wrong; probes check them.</span>
          {docsText ? <p className="mt-1 font-mono text-xs text-ink-2">{docsText.length} characters</p> : null}
          {fieldErrors.docs ? (
            <p data-testid="error-docs" className={FIELD_ERROR}>
              {fieldErrors.docs}
            </p>
          ) : null}
        </div>

        {/* Sandbox base URL */}
        <div>
          <label htmlFor="connector-base-url" className={FIELD_LABEL}>
            Sandbox base URL <span className="text-live" aria-hidden="true">*</span>
          </label>
          <input
            id="connector-base-url"
            data-testid="input-base-url"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://sim-legacystay:8000"
            list="base-url-suggestions"
            className={TEXT_INPUT}
            aria-invalid={Boolean(fieldErrors.base_url)}
            aria-describedby={fieldErrors.base_url ? "error-base-url" : undefined}
          />
          <datalist id="base-url-suggestions">
            {BASE_URL_SUGGESTIONS.map((u) => (
              <option key={u} value={u} />
            ))}
          </datalist>
          {fieldErrors.base_url ? (
            <p id="error-base-url" data-testid="error-base-url" className={FIELD_ERROR}>
              {fieldErrors.base_url}
            </p>
          ) : null}
        </div>

        {/* Credential env-ref picker */}
        <div>
          <label htmlFor="connector-credential-ref" className={FIELD_LABEL}>
            Credential <span className="text-live" aria-hidden="true">*</span>
          </label>
          <div className="mt-2 flex gap-2.5">
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
              className={TEXT_INPUT + " mt-0 flex-1 appearance-none"}
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
                className={TEXT_INPUT + " mt-0 flex-1"}
                aria-label="Custom credential env-ref"
              />
            ) : null}
          </div>
          <span className={FIELD_HINT}>
            An environment variable name. The value never reaches the browser or a model prompt.
          </span>
          {fieldErrors.credential_ref ? (
            <p id="error-credential-ref" data-testid="error-credential-ref" className={FIELD_ERROR}>
              {fieldErrors.credential_ref}
            </p>
          ) : null}
        </div>

        {/* Target entities */}
        <fieldset>
          <legend className={FIELD_LABEL}>
            Entities <span className="text-live" aria-hidden="true">*</span>
          </legend>
          <div className="mt-2 flex flex-wrap gap-2.5">
            {ENTITY_OPTIONS.map((entity) => {
              const checked = targetEntities.includes(entity);
              return (
                <label
                  key={entity}
                  className={`flex cursor-pointer items-center gap-2.5 border-[1.5px] px-4 py-2.5 font-mono text-[15px] ${
                    checked ? "border-ink text-ink" : "border-off text-ink-2"
                  }`}
                >
                  <input
                    data-testid={`checkbox-entity-${entity}`}
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleEntity(entity)}
                    className="h-4 w-4 accent-live"
                  />
                  {entity}
                </label>
              );
            })}
          </div>
          {fieldErrors.target_entities ? (
            <p data-testid="error-target-entities" className={FIELD_ERROR}>
              {fieldErrors.target_entities}
            </p>
          ) : null}
        </fieldset>

        {/* Spec vocabulary (REQ-021 item 2) */}
        <fieldset>
          <legend className={FIELD_LABEL}>Spec vocabulary</legend>
          <div className="mt-2 flex flex-wrap gap-2.5">
            {(["v1", "v2"] as const).map((v) => (
              <label
                key={v}
                className={`flex cursor-pointer items-center gap-2.5 border-[1.5px] px-4 py-2.5 font-mono text-[15px] ${
                  vocabulary === v ? "border-ink text-ink" : "border-off text-ink-2"
                }`}
              >
                <input
                  data-testid={`radio-vocabulary-${v}`}
                  type="radio"
                  name="spec-vocabulary"
                  value={v}
                  checked={vocabulary === v}
                  onChange={() => setVocabulary(v)}
                  className="h-4 w-4 accent-live"
                />
                {v}
              </label>
            ))}
          </div>
          <span className={FIELD_HINT}>
            v1 refuses unfamiliar API styles deterministically (0 model calls); v2 additionally compiles POST
            with a JSON body, body-field auth and body-cursor pagination.
          </span>
        </fieldset>

        {/* Submit */}
        <div className="flex flex-wrap items-center gap-4 border-t border-rule pt-6">
          <button
            type="submit"
            data-testid="submit-build"
            disabled={isBusy}
            className="btn disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitState === "creating"
              ? "Creating connector…"
              : submitState === "building"
                ? "Starting build…"
                : "Start build"}
          </button>
          <a href="/connectors" data-testid="cancel-link" className="link">
            Cancel
          </a>
          {isBusy ? (
            <span data-testid="busy-indicator" className="font-mono text-sm text-ink-2">
              Please wait…
            </span>
          ) : null}
        </div>
      </form>

      {/* Error */}
      {errorMsg ? (
        <div data-testid="submit-error" className="mt-6 border border-stop bg-band px-4 py-3 text-[15px] text-stop">
          {errorMsg}
          {result?.connectorId ? (
            <div className="mt-2">
              Connector <code className="bg-ground px-1 py-0.5 font-mono text-xs">{result.connectorId}</code> was created.{" "}
              <a href={`/connectors/${result.connectorId}`} className="underline">
                View connector
              </a>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Success */}
      {submitState === "done" && result ? (
        <div data-testid="submit-success" className="mt-6 border border-ink bg-band px-5 py-4 text-[15px] text-ink">
          <p className="font-bold">Connector created and build started.</p>
          <p className="mt-1 font-mono text-sm text-ink-2" data-testid="submit-vocabulary">
            Connector {result.connectorId} · vocabulary {result.vocabulary}
            {result.jobId ? <> · job {result.jobId}</> : null}
          </p>
          <div className="mt-3 flex flex-wrap gap-4">
            <a
              data-testid="success-view-connector"
              href={`/connectors/${result.connectorId}`}
              className="btn !px-4 !py-2.5 !text-[15px]"
            >
              View connector
            </a>
            <a data-testid="success-view-connectors" href="/connectors" className="link self-center">
              Back to list
            </a>
          </div>
        </div>
      ) : null}
    </div>
  );
}
