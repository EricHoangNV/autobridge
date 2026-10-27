"use client";

import type { DiscoveryRecordOut } from "@/lib/api/client";

// Discovered-vendor list (P2.S3.1, plan §5 S3): static DiscoveryResult
// imports and their explicit-endpoint lifecycle. Square-shape badges, never
// hue alone (matches HealthBadge broadcast language).

const ORDER = ["discovered", "needs_confirmation", "authorized", "connected", "healing"];

function shapeFor(status: string): string {
  switch (status) {
    case "discovered":
      return "st-mut";
    case "needs_confirmation":
      return "st-deg";
    case "authorized":
      return "st-deg";
    case "connected":
      return "st-ok";
    case "healing":
      return "st-blk";
    default:
      return "st-mut";
  }
}

function labelFor(status: string): string {
  return status === "needs_confirmation" ? "needs confirmation" : status;
}

export function DiscoveryList({ records }: { records: DiscoveryRecordOut[] }) {
  const sorted = [...records].sort(
    (a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status),
  );
  return (
    <section aria-label="discovered vendors" data-testid="discovery-list">
      <h2>Discovered vendors</h2>
      {sorted.length === 0 ? (
        <p data-testid="discovery-empty">No discovered vendors yet.</p>
      ) : (
        <ul>
          {sorted.map((r) => (
            <li key={r.discovery_id} data-testid={`discovery-${r.discovery_id}`}>
              <span data-testid="discovery-vendor">{r.vendor}</span>{" "}
              <span data-testid="discovery-status" className={`st ${shapeFor(r.status)}`}>
                {labelFor(r.status)}
              </span>{" "}
              <span data-testid="discovery-entities">{r.candidate_entities.join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
