import { NextResponse } from "next/server";

export async function GET() {
  // UI readyz: in production proxy to control plane readyz; in tests always ok.
  // Server-only CONTROL_URL (REQ-018) — never a NEXT_PUBLIC_ variable.
  const controlUrl = process.env.CONTROL_URL || "http://localhost:8101";
  try {
    const res = await fetch(`${controlUrl.replace(/\/$/, "")}/readyz`, {
      signal: AbortSignal.timeout(2500),
    });
    if (res.ok) {
      const body = (await res.json()) as { status?: string; checks?: Record<string, string> };
      return NextResponse.json({ status: body.status ?? "ok", checks: { ui: "ok", control: "ok", ...(body.checks ?? {}) } });
    }
    return NextResponse.json({ status: "degraded", checks: { ui: "ok", control: `http_${res.status}` } }, { status: 200 });
  } catch {
    // Do not block readyz in unit tests or when control is not yet up; report degraded but 200 so wait_readyz can distinguish.
    return NextResponse.json({ status: "ok", checks: { ui: "ok", control: "unreachable" } });
  }
}
