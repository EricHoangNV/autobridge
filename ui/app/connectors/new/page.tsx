import { NewConnectorForm } from "@/components/NewConnectorForm";

const RUN_SHEET: { title: string; detail: string; you?: boolean }[] = [
  {
    title: "Read the docs",
    detail: "List every endpoint, field and example, and flag what the docs leave open.",
  },
  {
    title: "Probe the sandbox, GET only",
    detail: "One page of each list endpoint to see real shapes and pagination.",
  },
  {
    title: "Settle what's ambiguous",
    detail: "Hypotheses for dates, pagination, money and status codes; the probe that splits them best runs next.",
  },
  {
    title: "Compile and verify",
    detail: "Write the spec, run visible tests, send it to the verifier's hidden fixtures.",
  },
  {
    title: "You approve release r1",
    detail: "Nothing syncs into your systems before that.",
    you: true,
  },
];

export default function NewConnectorPage() {
  return (
    <div data-testid="new-connector-page">
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,1fr)]">
        <div className="px-5 pb-14 pt-8 sm:px-8">
          <div className="mb-7">
            <p className="font-mono text-[13px] text-ink-2">
              <a
                href="/connectors"
                data-testid="back-to-connectors"
                className="text-ink-2 hover:text-ink hover:underline"
              >
                Connectors
              </a>{" "}
              / new
            </p>
            <h1
              className="mt-2 font-bold leading-none tracking-tight"
              style={{ fontSize: "clamp(34px, 3.4vw, 44px)", fontVariationSettings: '"wdth" 125' }}
            >
              Build a connector
            </h1>
            <p className="mt-3 max-w-[54ch] text-[17px] leading-snug text-ink-2">
              Give AutoBridge the vendor&apos;s docs and a sandbox. It probes with GET only, writes a spec, and
              asks the verifier before you see a release.
            </p>
          </div>
          <div className="max-w-[560px]">
            <NewConnectorForm />
          </div>
        </div>

        <aside aria-labelledby="run-sheet-title" className="border-t border-rule bg-band px-5 py-8 sm:px-8 lg:border-l lg:border-t-0">
          <h2 id="run-sheet-title" data-testid="run-sheet-title" className="kicker">
            What the build will do
          </h2>
          <ol data-testid="run-sheet" className="mb-7 list-none p-0">
            {RUN_SHEET.map((step, i) => (
              <li
                key={step.title}
                className="grid grid-cols-[52px_1fr] gap-x-3 border-b border-rule py-3.5 first:border-t"
              >
                <span
                  aria-hidden="true"
                  className={`row-span-2 text-[34px] font-black leading-none ${step.you ? "text-live" : "text-ink"}`}
                  style={{ fontVariationSettings: '"wdth" 125' }}
                >
                  {i + 1}
                </span>
                <span className="text-base font-bold">{step.title}</span>
                <span className="text-[15px] text-ink-2">{step.detail}</span>
              </li>
            ))}
          </ol>
          <button
            type="submit"
            form="build-connector-form"
            data-testid="sheet-start-build"
            className="btn w-full text-center"
          >
            Start build
          </button>
          <p className="mt-3 text-sm text-ink-2">
            Probes run GET only. Nothing syncs before you approve the release.
          </p>
        </aside>
      </div>
    </div>
  );
}
