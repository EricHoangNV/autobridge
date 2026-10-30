"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

/**
 * Broadcast shell top bar (REQ-002 slice 1): wordmark, section nav,
 * LIVE indicator (UI /healthz reachability), labelled demo inject button,
 * healthz link. Only same-origin UI routes are fetched from here.
 */
export function TopBar() {
  const pathname = usePathname();
  const [alive, setAlive] = useState<boolean | null>(null);
  const [localMode, setLocalMode] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/healthz", { cache: "no-store" })
      .then((res) => {
        if (!cancelled) setAlive(res.ok);
      })
      .catch(() => {
        if (!cancelled) setAlive(false);
      });
    // REQ-017: surface the runtime mode from the control plane (same-origin
    // /api rewrite, server-only CONTROL_URL — never a browser secret). Local
    // processes share one loopback, so Docker network isolation is absent.
    fetch("/api/readyz", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && data && typeof data === "object") {
          setLocalMode((data as { runtime_mode?: unknown }).runtime_mode === "local");
        }
      })
      .catch(() => {
        /* offline — badge stays hidden */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function handleDemoInject() {
    const drawer = document.querySelector('[data-testid="inject-fault-drawer"]');
    if (drawer) {
      if (typeof drawer.scrollIntoView === "function") drawer.scrollIntoView({ block: "start" });
      const toggle = drawer.querySelector<HTMLButtonElement>('[data-testid="inject-fault-toggle"]');
      const panel = drawer.querySelector('[data-testid="inject-fault-panel"]');
      if (toggle && !panel) toggle.click();
      return;
    }
    window.location.href = "/repairs";
  }

  const onConnectors = pathname === "/" || pathname.startsWith("/connectors");

  return (
    <header data-testid="topbar" className="border-b border-rule bg-ground">
      <div className="flex h-14 items-center gap-7 px-10 max-[900px]:gap-4 max-[900px]:px-4">
        <a
          href="/connectors"
          className="font-wide text-lg font-extrabold tracking-wide text-ink no-underline"
        >
          AUTOBRIDGE
        </a>
        <nav className="flex min-w-0 gap-5 text-sm" aria-label="Sections">
          <a
            href="/connectors"
            aria-current={onConnectors ? "page" : undefined}
            className={
              onConnectors
                ? "border-b-2 border-live pb-4 pt-[18px] font-semibold text-ink no-underline"
                : "border-b-2 border-transparent pb-4 pt-[18px] text-ink-2 no-underline"
            }
          >
            Connectors
          </a>
          <a
            href="/repairs"
            aria-current={pathname.startsWith("/repairs") ? "page" : undefined}
            className={
              pathname.startsWith("/repairs")
                ? "border-b-2 border-live pb-4 pt-[18px] font-semibold text-ink no-underline"
                : "border-b-2 border-transparent pb-4 pt-[18px] text-ink-2 no-underline"
            }
          >
            Repairs
          </a>
          <a
            href="/knowledge"
            aria-current={pathname.startsWith("/knowledge") ? "page" : undefined}
            className={
              pathname.startsWith("/knowledge")
                ? "border-b-2 border-live pb-4 pt-[18px] font-semibold text-ink no-underline"
                : "border-b-2 border-transparent pb-4 pt-[18px] text-ink-2 no-underline"
            }
          >
            Knowledge
          </a>
        </nav>
        <div className="ml-auto flex min-w-0 shrink-0 items-center gap-[18px]">
          <span
            data-testid="topbar-live"
            className={`flex items-center gap-2 text-[13px] font-bold tracking-widest ${
              alive === false ? "text-stop" : "text-ink"
            }`}
          >
            <span aria-hidden="true" className={`live-dot ${alive === false ? "is-off" : ""}`} /          >
            {alive === false ? "OFFLINE" : "LIVE"}
          </span>
          {localMode && (
            <span
              data-testid="topbar-local-mode"
              title="local mode: no network isolation (make local-up, no Docker)"
              className="flex items-center gap-2 text-[13px] font-bold tracking-widest text-ink-2"
            >
              LOCAL
            </span>
          )}
          <button
            type="button"
            data-testid="topbar-demo-inject"
            onClick={handleDemoInject}
            className="demo-btn px-2.5 py-1.5 text-[13px] font-medium"
          >
            Demo: inject fault
          </button>
          <a
            data-testid="topbar-healthz"
            href="/simulators"
            title={alive === false ? "Control API unreachable" : "All services healthy"}
            className="text-[13px] text-ink-2 no-underline hover:text-ink hover:underline"
          >
            System status
          </a>
        </div>
      </div>
    </header>
  );
}
