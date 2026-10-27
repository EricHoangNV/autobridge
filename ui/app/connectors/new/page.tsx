import { NewConnectorForm } from "@/components/NewConnectorForm";

export default function NewConnectorPage() {
  return (
    <div data-testid="new-connector-page">
      <div className="mb-6">
        <a
          href="/connectors"
          data-testid="back-to-connectors"
          className="text-xs text-ink-2 hover:text-ink hover:underline"
        >
          ← Back to connectors
        </a>
        <h1 className="mt-1 text-xl font-semibold tracking-tight">New connector</h1>
        <p className="mt-1 text-sm text-ink-2">
          Point at a sandbox, pick a credential env-ref, choose entities and attach vendor docs to start a build.
        </p>
      </div>
      <div className="max-w-2xl border bg-band p-6 shadow-sm">
        <NewConnectorForm />
      </div>
    </div>
  );
}
