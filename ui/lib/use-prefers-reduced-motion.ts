"use client";

/**
 * `prefers-reduced-motion: reduce` as React state (REQ-002 slice 3).
 * False on the server and where `matchMedia` is unavailable (jsdom), so the
 * climb animates by default and snaps to the final state only when asked.
 */
import { useEffect, useState } from "react";

export function usePrefersReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduce(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduce(e.matches);
    try {
      mq.addEventListener("change", onChange);
    } catch {
      return;
    }
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return reduce;
}
