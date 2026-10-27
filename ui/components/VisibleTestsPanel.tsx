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
      <div data-testid="visible-tests-empty" className="border border-dashed border-rule px-4 py-6 text-center text-sm text-ink-2">
        No visible tests recorded for this repair.
      </div>
    );
  }

  const passedCount = results.filter((t) => testPassed(t) === true).length;
  const failedCount = results.filter((t) => testPassed(t) === false).length;

  return (
    <div data-testid="visible-tests-panel">
      <div className="mb-1 flex items-center gap-2 text-xs text-ink-2">
        {passedCount > 0 ? (
          <span data-testid="visible-tests-passed-count">{passedCount} passed</span>
        ) : null}
        {failedCount > 0 ? (
          <span data-testid="visible-tests-failed-count" className="text-stop">{failedCount} failed</span>
        ) : null}
        {passedCount === 0 && failedCount === 0 ? <span>{results.length} recorded</span> : null}
      </div>

      <ul className="repair-tests">
        {results.map((t, idx) => {
          const passed = testPassed(t);
          const name = (t.name ?? t.test ?? `test-${idx}`) as string;
          const shape = passed === true ? "st ok" : passed === false ? "st blk" : "st";
          return (
            <li key={idx} data-testid={`visible-test-${idx}`}>
              <code data-testid={`visible-test-name-${idx}`}>{name}</code>
              <span data-testid={`visible-test-status-${idx}`} className={shape}>
                {passed === true ? "pass" : passed === false ? "fail" : "unknown"}
              </span>
            </li>
          );
        })}
      </ul>
      {/* Extra detail (message / error / unknown keys) below the broadcast list */}
      <div className="mt-2 space-y-1">
        {results.map((t, idx) => (
          <div key={idx}>
            {t.message ? (
              <div className="text-xs text-ink-2">{String(t.message)}</div>
            ) : null}
            {t.error ? (
              <div className="bg-band px-2 py-1 font-mono text-xs text-stop">{String(t.error)}</div>
            ) : null}
            {(() => {
              const known = new Set(["name", "test", "status", "passed", "ok", "result", "message", "error"]);
              const extra: Record<string, unknown> = {};
              for (const [k, v] of Object.entries(t)) {
                if (!known.has(k)) extra[k] = v;
              }
              if (Object.keys(extra).length) {
                return (
                  <pre className="mt-1 max-h-24 overflow-auto bg-band p-2 font-mono text-[11px] text-ink-2">
                    {JSON.stringify(extra, null, 2)}
                  </pre>
                );
              }
              return null;
            })()}
          </div>
        ))}
      </div>
    </div>
  );
}
