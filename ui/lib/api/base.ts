/**
 * Same-origin control-API base (REQ-018).
 *
 * The browser NEVER talks to the control plane directly. Every control call
 * goes to same-origin `/api/...`, which Next.js rewrites to the control API
 * (see `ui/next.config.mjs`) and `ui/middleware.ts` attaches the control
 * token server-side — so the token never ships to the browser.
 *
 * Server-side (SSR / route handlers) there is no browser origin, so use the
 * server-only `CONTROL_URL` (compose sets it to the control service;
 * plain `npm run dev` / REQ-017 local mode falls back to loopback :8101).
 *
 * NOTE (REQ-018 bundle guard): keep literal control hostnames out of this
 * file, even in comments — dev-mode chunks serve the source verbatim.
 * `NEXT_PUBLIC_*` must stay out of here: anything read via `process.env`
 * in browser code is inlined into the `.next/static` bundle at build time.
 */
export function apiBaseUrl(): string {
  if (typeof window !== "undefined") return "/api";
  const control = typeof process !== "undefined" ? process.env.CONTROL_URL : undefined;
  return (control ?? "http://localhost:8101").replace(/\/$/, "");
}
