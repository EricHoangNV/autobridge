/**
 * The in-browser "Inject fault" demo controls (top bar button + repair page
 * drawer) are hidden unless NEXT_PUBLIC_DEMO_INJECT_UI=1 at build time. The
 * presenter breaks simulators from the terminal (`make demo-break`); the
 * control endpoint POST /demo/inject-fault is unchanged.
 */
export function demoInjectUiEnabled(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_INJECT_UI === "1";
}
