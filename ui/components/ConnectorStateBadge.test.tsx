import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConnectorStateBadge } from "./ConnectorStateBadge";

describe("ConnectorStateBadge", () => {
  it("renders working as green circle + word", () => {
    render(<ConnectorStateBadge state="working" />);
    const badge = screen.getByTestId("connector-state");
    expect(badge).toHaveAttribute("data-state", "working");
    // Colour + shape + word, never colour alone.
    expect(badge).toHaveAttribute("data-shape", "circle");
    expect(badge.className).toContain("cs-ok");
    expect(badge.textContent).toContain("Working");
  });

  it("renders degrading as yellow triangle + word", () => {
    render(<ConnectorStateBadge state="degrading" activity="drift detected 4 s ago · repairing at L1" />);
    const badge = screen.getByTestId("connector-state");
    expect(badge).toHaveAttribute("data-shape", "triangle");
    expect(badge.className).toContain("cs-deg");
    expect(badge.textContent).toContain("Degrading");
    expect(screen.getByTestId("connector-state-activity").textContent).toContain("drift detected");
  });

  it("renders error as red square + word", () => {
    render(<ConnectorStateBadge state="error" activity="Blocked: auth revoked" />);
    const badge = screen.getByTestId("connector-state");
    expect(badge).toHaveAttribute("data-shape", "square");
    expect(badge.className).toContain("cs-err");
    expect(badge.textContent).toContain("Error");
  });

  it("ticks the last-sync age", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T12:00:00Z"));
    try {
      const fourSecondsAgo = new Date("2026-10-29T11:59:56Z").toISOString();
      render(<ConnectorStateBadge state="working" lastSyncAt={fourSecondsAgo} />);
      expect(screen.getByTestId("connector-state-sync-age").textContent).toContain("last sync 4 s ago");
    } finally {
      vi.useRealTimers();
    }
  });

  it("omits the age line when there is no sync yet", () => {
    render(<ConnectorStateBadge state="degrading" />);
    expect(screen.queryByTestId("connector-state-sync-age")).toBeNull();
  });

  it("says syncs are paused when a decision waits (REQ-067 item 9)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T12:03:00Z"));
    try {
      const threeMinutesAgo = new Date("2026-10-29T12:00:00Z").toISOString();
      render(<ConnectorStateBadge state="error" activity="needs your decision" lastSyncAt={threeMinutesAgo} />);
      expect(screen.getByTestId("connector-state-sync-age").textContent).toContain("syncs paused until you decide");
      expect(screen.getByTestId("connector-state-sync-age").textContent).not.toContain("last sync");
    } finally {
      vi.useRealTimers();
    }
  });

  it("says syncs are paused for a verified fix waiting for review (REQ-067 item 9)", () => {
    const threeMinutesAgo = new Date(Date.now() - 180000).toISOString();
    render(<ConnectorStateBadge state="degrading" activity="fix waiting for review" lastSyncAt={threeMinutesAgo} />);
    expect(screen.getByTestId("connector-state-sync-age").textContent).toContain("syncs paused until you decide");
  });

  it("keeps the age for errors that still sync (REQ-067 item 9)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T12:00:00Z"));
    try {
      const fourSecondsAgo = new Date("2026-10-29T11:59:56Z").toISOString();
      render(<ConnectorStateBadge state="error" activity="Blocked: auth revoked" lastSyncAt={fourSecondsAgo} />);
      expect(screen.getByTestId("connector-state-sync-age").textContent).toContain("last sync 4 s ago");
    } finally {
      vi.useRealTimers();
    }
  });
});
