"use client";

import { useState, useRef, useEffect, type FormEvent } from "react";

const KNOWN_CREDENTIAL_REFS = ["ATLAS_API_KEY", "HARBOR_API_KEY", "MERIDIAN_API_KEY", "LEGACYSTAY_API_KEY", "EDGE_TOKEN", "ATLAS_BASE_URL", "HARBOR_BASE_URL"];
const ENTITY_OPTIONS = ["Reservation", "FolioCharge"] as const;
// Relay address the engine's framed (FIAS) builds poll through. The build
// page never asks for it: choosing the PABX docs fills it in, and the
// engine decides the runtime from the docs (REQ-050 item 6).
const EDGE_RELAY_BASE_URL = "edge-relay://queue/fias-commands";
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
  // REQ-050 item 6: the page sends no runtime choice — the engine decides
  // where the connector runs from the docs (HTTP → cloud, framed/FIAS →
  // the on-premise edge agent), and the result displays that decision.
};

type SubmitState = "idle" | "creating" | "building" | "done" | "error";

type SimRow = {
  id: string;
  display_name: string;
  docs_source: string;
  developers_url: string;
};

/** Build progress (REQ-031 item 4): docs read → draft → probes → result → release.
 *
 * Polls the build job until terminal, then reads the persisted build run.
 * Every stage renders stored rows (job result, build-run detail), never
 * recomputed text.
 */
function BuildProgress({
  connectorId,
  jobId,
  buildRunId,
  docsChars,
}: {
  connectorId: string;
  jobId?: string;
  buildRunId?: string;
  docsChars?: number;
}) {
  const [jobState, setJobState] = useState<string>("starting");
  const [detail, setDetail] = useState<{
    draft_spec?: Record<string, unknown> | null;
    result?: Record<string, unknown>;
    ladder_trace?: Array<Record<string, unknown>>;
    inputs?: Record<string, unknown>;
  } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId || !buildRunId) return;
    let cancelled = false;
    let polls = 0;
    async function poll() {
      const base = getBaseUrl().replace(/\/$/, "");
      try {
        const res = await fetch(`${base}/jobs/${encodeURIComponent(jobId as string)}`);
        if (!res.ok) throw new Error(`job read failed (${res.status})`);
        const job = (await res.json()) as { state?: string };
        if (cancelled) return;
        setJobState(String(job.state || "unknown"));
        if (job.state === "succeeded" || job.state === "failed") {
          const dres = await fetch(`${base}/runs/${encodeURIComponent(buildRunId as string)}/detail`);
          if (!dres.ok) throw new Error(`build detail read failed (${dres.status})`);
          const body = (await dres.json()) as { run?: typeof detail };
          if (!cancelled) setDetail(body.run ?? null);
          return;
        }
      } catch (err) {
        if (!cancelled) setFailed(err instanceof Error ? err.message : String(err));
        return;
      }
      polls += 1;
      if (!cancelled && polls < 60) window.setTimeout(poll, 2000);
      else if (!cancelled) setFailed("build job did not finish in time");
    }
    void poll();
    return () => {
      cancelled = true;
    };
  }, [jobId, buildRunId]);

  const result = detail?.result ?? {};
  const draft = (detail?.draft_spec ?? null) as Record<string, unknown> | null;
  const trace = Array.isArray(detail?.ladder_trace) ? detail.ladder_trace : [];
  const stageNames = trace.map((t) =>
    String(t.stage ?? t.action ?? t.rung ?? "?"),
  );
  const success = result.success === true;
  const releaseId = typeof result.release_id === "string" ? result.release_id : null;
  // REQ-050 item 6: the engine decides where the connector runs from the
  // docs — the page displays the decision, never a setting.
  const runtimeLine = detail ? runtimeDecisionLine(draft) : `job ${jobState}…`;

  // REQ-049 item 2: when discovery added a money check from a total the
  // docs describe, the build page names it (never invented silently).
  const pageTotalDetail = trace
    .filter((t) => String(t.stage ?? "") === "page_total_check" && typeof t.detail === "string")
    .map((t) => String(t.detail))
    .filter((d) => d.length > 0)
    .join(" · ");

  const stages: Array<{ key: string; title: string; body: string }> = [
    {
      key: "docs",
      title: "Docs read",
      body:
        detail?.inputs && typeof detail.inputs.source === "string"
          ? `${docsChars ?? "?"} chars · ${detail.inputs.source}`
          : jobState === "starting"
            ? "starting…"
            : `${docsChars ?? "?"} chars sent`,
    },
    {
      key: "draft",
      title: "Draft",
      body:
        (detail
          ? draft
            ? `spec v${String((draft.spec_version as string) ?? "?")} · ${Object.keys((draft.operations as object) ?? {}).length} operations`
            : "no draft (refused)"
          : `job ${jobState}…`) + (pageTotalDetail ? ` — ${pageTotalDetail}` : ""),
    },
    {
      key: "probes",
      title: "Probes",
      body: detail
        ? trace.length > 0
          ? `${trace.length} trace stages: ${stageNames.slice(0, 8).join(" → ")}${stageNames.length > 8 ? " …" : ""}`
          : "no probe stages recorded"
        : `job ${jobState}…`,
    },
    {
      key: "result",
      title: "Result",
      body: detail
        ? success
          ? result.verified === true
            ? "verified by the hidden fixtures"
            : "built (verifier did not gate this build)"
          : String(result.degradation_reason || result.gate_blocked || "build failed")
        : `job ${jobState}…`,
    },
    {
      key: "release",
      title: "Release",
      body: detail
        ? releaseId
          ? `release ${releaseId}`
          : "no release persisted"
        : `job ${jobState}…`,
    },
  ];

  return (
    <div data-testid="build-progress" className="mt-4 border-t border-rule pt-4">
      <p className="font-bold">Build progress</p>
      <p data-testid="build-runtime-decision" className="mt-1 font-mono text-[13px] text-ink-2">
        {runtimeLine}
      </p>
      {failed ? (
        <p data-testid="build-progress-error" className="mt-1 text-[15px] text-stop">
          {failed}
        </p>
      ) : null}
      <ol className="mt-2 list-none space-y-2 p-0">
        {stages.map((s, i) => (
          <li key={s.key} data-testid={`build-stage-${s.key}`} className="flex gap-3 text-[15px]">
            <span aria-hidden="true" className="font-mono font-bold text-ink-2">
              {i + 1}.
            </span>
            <span>
              <span className="font-bold">{s.title}.</span> <span className="text-ink-2">{s.body}</span>
              {s.key === "release" && releaseId ? (
                <span>
                  {" "}
                  <a data-testid="build-release-link" href={`/connectors/${connectorId}`} className="link">
                    View connector
                  </a>
                </span>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

const FIELD_LABEL = "block text-[15px] font-bold text-ink";
const FIELD_HINT = "mt-1.5 block text-sm text-ink-2";
const TEXT_INPUT =
  "mt-2 w-full border-[1.5px] border-off bg-ground px-3.5 py-3 font-mono text-base text-ink placeholder:text-ink-2/60 focus:border-live focus:outline-none";
const FIELD_ERROR = "mt-1.5 text-sm text-stop";

// One line per language release, shared with the Knowledge page
// (ui/app/knowledge/page.tsx RELEASE_NOTE): the chain reads as a
// timeline, and the engine refuses docs a vocabulary cannot express.
const VOCABULARY_NOTES: Record<"v1" | "v2" | "v3", string> = {
  v1: "REST reads: GET with headers or basic auth",
  v2: "request bodies and joins (POST-style APIs)",
  v3: "event streams over TCP (on-premise devices)",
};

/** Where the engine runs a built connector, decided from the built spec
 * (REQ-050 item 6): framed/event docs carry
 * ``connector.runtime_target: "edge"`` and a non-HTTP protocol (the
 * on-premise edge agent, via the relay); HTTP docs run in the cloud.
 * Never a page setting — the page only displays the decision. */
export function runtimeDecisionForDraft(draft: unknown): "edge" | "cloud" {
  if (draft !== null && typeof draft === "object" && !Array.isArray(draft)) {
    const connector = (draft as Record<string, unknown>)["connector"];
    if (
      connector !== null &&
      typeof connector === "object" &&
      !Array.isArray(connector) &&
      (connector as Record<string, unknown>)["runtime_target"] === "edge"
    ) {
      return "edge";
    }
    const source = (draft as Record<string, unknown>)["source"];
    if (source !== null && typeof source === "object" && !Array.isArray(source)) {
      const protocol = (source as Record<string, unknown>)["protocol"];
      if (typeof protocol === "string" && protocol !== "" && protocol !== "http") return "edge";
    }
  }
  return "cloud";
}

export function runtimeDecisionLine(draft: unknown): string {
  return runtimeDecisionForDraft(draft) === "edge"
    ? "runs: on-premise edge agent, via the relay"
    : "runs: cloud";
}

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
  // Spec vocabulary (REQ-021 item 2; REQ-022 C: v3 for framed/event docs)
  const [vocabulary, setVocabulary] = useState<"v1" | "v2" | "v3">("v1");

  // Simulator docs picker (REQ-031 item 4): the registry simulators from
  // GET /demo/simulators. Choosing one loads the developers-portal text
  // through the control plane (GET /demo/docs?target=) into the textarea,
  // which the build then sends as docs_text — the browser never fetches a
  // caller-supplied URL and the server never fetches one either.
  const [simRows, setSimRows] = useState<SimRow[]>([]);
  const [simDocsId, setSimDocsId] = useState("");
  const [simDocsLoading, setSimDocsLoading] = useState(false);
  const [simDocsError, setSimDocsError] = useState<string | null>(null);
  const [simDocsInfo, setSimDocsInfo] = useState<{
    display_name: string;
    docs_source: string;
    characters: number;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadSims() {
      try {
        const base = getBaseUrl().replace(/\/$/, "");
        const res = await fetch(`${base}/demo/simulators`);
        if (!res.ok) return;
        const body = (await res.json()) as { simulators?: SimRow[] };
        if (!cancelled && Array.isArray(body.simulators)) setSimRows(body.simulators);
      } catch {
        // Picker stays empty; upload/paste/URL still work.
      }
    }
    loadSims();
    return () => {
      cancelled = true;
    };
  }, []);

  async function chooseSimDocs(id: string) {
    setSimDocsId(id);
    setSimDocsError(null);
    if (!id) {
      setSimDocsInfo(null);
      return;
    }
    const row = simRows.find((r) => r.id === id);
    setSimDocsLoading(true);
    try {
      const base = getBaseUrl().replace(/\/$/, "");
      const res = await fetch(`${base}/demo/docs?target=${encodeURIComponent(id)}`);
      if (!res.ok) throw new Error(`docs load failed (${res.status})`);
      const body = (await res.json()) as {
        docs_text?: string;
        display_name?: string;
        docs_source?: string;
        characters?: number;
      };
      const text = typeof body.docs_text === "string" ? body.docs_text : "";
      if (!text) throw new Error("empty docs text");
      setDocsText(text);
      setFileContent(null);
      setFileName(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setSimDocsInfo({
        display_name: body.display_name || row?.display_name || id,
        docs_source: body.docs_source || row?.docs_source || "",
        characters: body.characters ?? text.length,
      });
      const docsSource = body.docs_source || row?.docs_source || "";
      const pickedFias =
        id.toLowerCase().includes("fias") || docsSource.toLowerCase().includes("fias-pabx");
      if (pickedFias) {
        // REQ-050 item 6: no base-URL typing for the PABX on camera. The
        // relay address is fixed and the engine runs the connector on the
        // on-premise edge agent — decided from the docs, not a setting.
        // The PABX posts charges only, and event docs need v3 to compile.
        setBaseUrl(EDGE_RELAY_BASE_URL);
        setTargetEntities(["FolioCharge"]);
        setVocabulary((v) => (v === "v1" ? "v3" : v));
        if (!name.trim()) setName("PABX edge");
      } else if (!baseUrl.trim() && row?.developers_url) {
        setBaseUrl(row.developers_url.replace(/\/developers\/?$/, ""));
      }
    } catch (err) {
      setSimDocsError(err instanceof Error ? err.message : String(err));
      setSimDocsInfo(null);
    } finally {
      setSimDocsLoading(false);
    }
  }

  // REQ-050 item 6: the PABX docs mean the on-premise edge agent — the
  // base-URL field stays hidden and the relay address goes on the wire.
  const isFiasDocs =
    simDocsId.toLowerCase().includes("fias") ||
    (simDocsInfo?.docs_source ?? "").toLowerCase().includes("fias-pabx");

  // UI state
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [result, setResult] = useState<{
    connectorId: string;
    buildRunId?: string;
    jobId?: string;
    vocabulary: "v1" | "v2" | "v3";
    docsChars?: number;
  } | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const effectiveCredentialRef = useCustomCredential ? customCredential.trim() : credentialRef;

  function validate(): boolean {
    const errs: Record<string, string> = {};
    if (!name.trim()) errs.name = "Name is required";
    if (!isFiasDocs) {
      if (!baseUrl.trim()) errs.base_url = "Sandbox base URL is required";
      else {
        try {
          const parsed = new URL(baseUrl.trim());
          if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
            errs.base_url = "Must be an http(s) URL";
          }
        } catch {
          errs.base_url = "Must be a valid URL";
        }
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
      base_url: isFiasDocs ? EDGE_RELAY_BASE_URL : baseUrl.trim(),
      credential_ref: effectiveCredentialRef,
      vendor_docs_ref: docsUrl.trim() || simDocsInfo?.docs_source || (fileName ? `file:${fileName}` : null),
      target_entities: [...targetEntities],
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
      docsChars: (buildBody.docs_text as string | undefined)?.length ?? docsText.trim().length,
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
            {simRows.length > 0 ? (
              <label className="block">
                <span className={FIELD_HINT}>Or start from a simulator&apos;s developers portal</span>
                <select
                  data-testid="input-sim-docs"
                  value={simDocsId}
                  onChange={(e) => void chooseSimDocs(e.target.value)}
                  disabled={simDocsLoading}
                  className={TEXT_INPUT + " mt-0 appearance-none"}
                >
                  <option value="">Choose a simulator…</option>
                  {simRows.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.display_name} ({r.docs_source})
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {simDocsLoading ? (
              <p data-testid="sim-docs-loading" className="font-mono text-xs text-ink-2">
                Loading developers-portal docs…
              </p>
            ) : null}
            {simDocsError ? (
              <p data-testid="sim-docs-error" className={FIELD_ERROR}>
                {simDocsError}
              </p>
            ) : null}
            {simDocsInfo ? (
              <p data-testid="sim-docs-info" className="font-mono text-xs text-ink-2">
                {simDocsInfo.characters} chars from {simDocsInfo.display_name} developers portal
              </p>
            ) : null}
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

        {/* Sandbox base URL — hidden for the PABX (REQ-050 item 6): the
            relay address is fixed and the engine runs the connector on
            the on-premise edge agent, decided from the docs. */}
        {isFiasDocs ? (
          <div data-testid="edge-runtime-note">
            <span className={FIELD_LABEL}>Where it runs</span>
            <p className="mt-2 border border-rule bg-band px-3.5 py-3 font-mono text-base text-ink">
              {EDGE_RELAY_BASE_URL}
            </p>
            <span className={FIELD_HINT}>
              FIAS docs run on the on-premise edge agent, via the relay — decided by the engine from the
              docs, not a setting. No base URL to type.
            </span>
          </div>
        ) : (
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
        )}

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

        {/* Spec vocabulary (REQ-021 item 2; REQ-050 item 2: each version
            carries its one-line release note, shared with the Knowledge
            page; the current default is v1) */}
        <fieldset>
          <legend className={FIELD_LABEL}>Spec vocabulary</legend>
          <div className="mt-2 flex flex-col gap-2.5">
            {(["v1", "v2", "v3"] as const).map((v) => (
              <label
                key={v}
                className={`flex cursor-pointer items-start gap-2.5 border-[1.5px] px-4 py-2.5 font-mono text-[15px] ${
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
                  className="mt-1 h-4 w-4 accent-live"
                />
                <span>
                  <span className="font-bold">{v}</span>
                  <span className="text-ink-2"> — {VOCABULARY_NOTES[v]}</span>
                </span>
              </label>
            ))}
          </div>
          <span className={FIELD_HINT}>
            The engine refuses docs a vocabulary cannot express, deterministically at 0 model calls
            (v1 cannot do POST with a body, or event streams).
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
          <BuildProgress
            connectorId={result.connectorId}
            jobId={result.jobId}
            buildRunId={result.buildRunId}
            docsChars={result.docsChars}
          />
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
