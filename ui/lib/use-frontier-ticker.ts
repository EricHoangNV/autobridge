"use client";

/**
 * Frontier-token count-up for the scorebug (REQ-002 slice 3, "the climb").
 *
 * The counter holds at 0 until the ladder reaches L3 (`active`), then ticks
 * 0 → target over ~900 ms with an ease-out cubic — the prototype's L3 tick.
 * Under `prefers-reduced-motion` (or without `requestAnimationFrame`) it jumps
 * straight to the target, matching the direction's reduced-motion rule.
 */
import { useEffect, useState } from "react";
import { usePrefersReducedMotion } from "./use-prefers-reduced-motion";

const TICK_MS = 900;

export function useFrontierTicker(target: number, active: boolean): number {
  const reduce = usePrefersReducedMotion();
  const [shown, setShown] = useState(0);

  useEffect(() => {
    const safeTarget =
      typeof target === "number" && Number.isFinite(target) && target > 0 ? Math.round(target) : 0;
    if (!active || safeTarget === 0 || reduce || typeof requestAnimationFrame !== "function") {
      setShown(safeTarget);
      return;
    }
    let raf = 0;
    const t0 = typeof performance !== "undefined" ? performance.now() : 0;
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / TICK_MS);
      const eased = 1 - Math.pow(1 - p, 3);
      setShown(Math.round(safeTarget * eased));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [active, target, reduce]);

  return shown;
}
