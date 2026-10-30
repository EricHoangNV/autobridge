import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PatchDiff } from "./PatchDiff";

describe("PatchDiff", () => {
  it("shows empty when no patch", () => {
    render(<PatchDiff candidatePatch={null} />);
    expect(screen.getByTestId("patch-diff-empty")).toBeInTheDocument();
  });

  it("shows empty when empty array", () => {
    render(<PatchDiff candidatePatch={[]} />);
    expect(screen.getByTestId("patch-diff-empty")).toBeInTheDocument();
  });

  it("renders patch ops side by side", () => {
    const patch = [
      { op: "replace", path: "/operations/listReservations/pagination/strategy", value: "cursor" },
      { op: "add", path: "/health/canary/every", value: "30s" },
    ];
    render(<PatchDiff candidatePatch={patch} />);
    expect(screen.getByTestId("patch-diff")).toBeInTheDocument();
    expect(screen.getByTestId("patch-op-0")).toBeInTheDocument();
    expect(screen.getByTestId("patch-op-1")).toBeInTheDocument();
    expect(screen.getByTestId("patch-op-path-0")).toHaveTextContent("/operations/listReservations/pagination/strategy");
    expect(screen.getByTestId("patch-diff-raw")).toBeInTheDocument();
  });

  it("shows remove op correctly", () => {
    render(<PatchDiff candidatePatch={[{ op: "remove", path: "/foo" }]} />);
    expect(screen.getByTestId("patch-op-0")).toBeInTheDocument();
    expect(screen.getByTestId("patch-op-0").textContent).toContain("remove");
  });

  it("shows copy button", () => {
    render(<PatchDiff candidatePatch={[{ op: "replace", path: "/a", value: "b" }]} />);
    expect(screen.getByTestId("patch-copy-btn")).toBeInTheDocument();
  });

  it("has side-by-side old/new panels", () => {
    render(<PatchDiff candidatePatch={[{ op: "replace", path: "/x", value: 1 }]} />);
    expect(screen.getByTestId("patch-diff-old")).toBeInTheDocument();
    expect(screen.getByTestId("patch-diff-new")).toBeInTheDocument();
  });

  it("shows the field note next to the matching change (REQ-055)", () => {
    render(
      <PatchDiff
        candidatePatch={[{ op: "replace", path: "/entities/Reservation/checks/3/compare_to", value: "$.total" }]}
        fieldNotes={[{ path: "/entities/Reservation/checks/3/compare_to", note: "`$.total` is `30` in the current response (an integer)" }]}
      />,
    );
    expect(screen.getByTestId("patch-op-note-0")).toHaveTextContent("`$.total` is `30`");
  });

  it("shows no note when no field note matches", () => {
    render(
      <PatchDiff
        candidatePatch={[{ op: "replace", path: "/a", value: "b" }]}
        fieldNotes={[{ path: "/other", note: "elsewhere" }]}
      />,
    );
    expect(screen.queryByTestId("patch-op-note-0")).not.toBeInTheDocument();
  });
});
