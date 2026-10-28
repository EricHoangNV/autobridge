import { NextResponse, type NextRequest } from "next/server";

/**
 * Forward the control-plane write token server-side (REQ-012).
 *
 * The UI proxies `/api/*` to the control API via the rewrite in
 * `next.config.mjs`. When `AUTOBRIDGE_CONTROL_TOKEN` is set on the UI
 * container, state-changing requests (POST/PUT/PATCH/DELETE) get
 * `Authorization: Bearer <token>` injected here — in middleware, so the
 * token never ships to the browser (no `NEXT_PUBLIC_` variable).
 * Read-only GETs and the SSE stream pass through untouched.
 */
const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function middleware(request: NextRequest) {
  const token = process.env.AUTOBRIDGE_CONTROL_TOKEN;
  if (!token || !WRITE_METHODS.has(request.method)) {
    return NextResponse.next();
  }
  const headers = new Headers(request.headers);
  if (!headers.has("authorization")) {
    headers.set("authorization", `Bearer ${token}`);
  }
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: "/api/:path*",
};
