"use client";

type TestResult = {
  name?: string;
  test?: string;
  status?: string;
  passed?: boolean;
  ok?: boolean;
  result?: string;
  message?: string;
  error?: string;
  [key: string]: unknown;
};

type Props = {
  visibleTestResults: TestResult[] | null | undefined;
};

function testPassed(t: TestResult): boolean | null {
  if (typeof t.passed === "boolean") return t.passed;
  if (typeof t.ok === "boolean") return t.ok;
  if (typeof t.status === "string") {
    const s = t.status.toLowerCase();
    if (s === "pass" || s === "passed" || s === "ok" || s === "success") return true;
    if (s === "fail" || s === "failed" || s === "error") return false;
  }
  if (typeof t.result === "string") {
    const r = t.result.toLowerCase();
    if (r === "pass" || r === "passed") return true;
    if (r === "fail" || r === "failed") return false;
  }
  return null;
}

export function VisibleTestsPanel({ visibleTestResults }: Props) {
  const results = visibleTestResults ?? [];

  if (!results.length) {
    return (
      <div data-testid="visible-tests-empty" className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-zinc-500">
        No visible tests recorded for this repair.
      </div>
    );
  }

  const passedCount = results.filter((t) => testPassed(t) === true).length;
  const failedCount = results.filter((t) => testPassed(t) === false).length;

  return (
    <div data-testid="visible-tests-panel" className="overflow-hidden rounded-lg border bg-white">
      <div className="flex items-center justify-between border-b bg-zinc-50 px-4 py-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-600">
          Visible tests ({results.length})
        </h3>
        <div className="flex items-center gap-2 text-xs">
          {passedCount > 0 ? (
            <span data-testid="visible-tests-passed-count" className="inline-flex rounded-full bg-emerald-100 px-2 py-0.5 font-medium text-emerald-800">
              {passedCount} passed
            </span>
          ) : null}
          {failedCount > 0 ? (
            <span data-testid="visible-tests-failed-count" className="inline-flex rounded-full bg-red-100 px-2 py-0.5 font-medium text-red-800">
              {failedCount} failed
            </span>
          ) : null}
        </div>
      </div>

      <ul className="divide-y">
        {results.map((t, idx) => {
          const passed = testPassed(t);
          const name = (t.name ?? t.test ?? `test-${idx}`) as string;
          return (
            <li key={idx} data-testid={`visible-test-${idx}`} className="flex items-start gap-3 px-4 py-3">
              <span
                data-testid={`visible-test-status-${idx}`}
                className={`mt-0.5 inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
                  passed === true
                    ? "bg-emerald-50 text-emerald-800 border-emerald-200"
                    : passed === false
                      ? "bg-red-50 text-red-800 border-red-200"
                      : "bg-zinc-100 text-zinc-600 border-zinc-200"
                }`}
              >
                {passed === true ? "pass" : passed === false ? "fail" : "unknown"}
              </span>
              <div className="min-w-0 flex-1">
                <div data-testid={`visible-test-name-${idx}`} className="text-xs font-medium text-zinc-800">
                  {name}
                </div>
                {t.message ? (
                  <div className="mt-1 text-xs text-zinc-500">{String(t.message)}</div>
                ) : null}
                {t.error ? (
                  <div className="mt-1 rounded bg-red-50 px-2 py-1 font-mono text-xs text-red-700">{String(t.error)}</div>
                ) : null}
                {/* Show extra keys as compact JSON for unknown shapes */}
                {(() => {
                  const known = new Set(["name", "test", "status", "passed", "ok", "result", "message", "error"]);
                  const extra: Record<string, unknown> = {};
                  for (const [k, v] of Object.entries(t)) {
                    if (!known.has(k)) extra[k] = v;
                  }
                  if (Object.keys(extra).length) {
                    return (
                      <pre className="mt-1 max-h-24 overflow-auto rounded bg-zinc-50 p-2 font-mono text-[11px] text-zinc-600">
                        {JSON.stringify(extra, null, 2)}
                      </pre>
                    );
                  }
                  return null;
                })()}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
