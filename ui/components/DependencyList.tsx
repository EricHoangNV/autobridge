"use client";

import type { DependencyOut } from "@/lib/api/client";

type Props = {
  dependencies: DependencyOut[];
};

export function DependencyList({ dependencies }: Props) {
  if (!dependencies.length) {
    return (
      <div data-testid="dependency-list-empty" className="border border-dashed px-4 py-6 text-center text-sm text-ink-2">
        No dependency registrations. Consumers that depend on this connector will appear here.
      </div>
    );
  }

  return (
    <div data-testid="dependency-list" className="overflow-hidden border bg-band">
      <table data-testid="dependency-table" className="w-full text-left text-sm">
        <thead className="bg-ground text-xs uppercase tracking-wide text-ink-2">
          <tr>
            <th className="px-4 py-2 font-medium">Consumer</th>
            <th className="px-4 py-2 font-medium">Entity</th>
            <th className="px-4 py-2 font-medium">Policy</th>
            <th className="px-4 py-2 font-medium">Paused</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {dependencies.map((dep) => (
            <tr key={dep.id} data-testid={`dependency-row-${dep.id}`} className="hover:bg-ground">
              <td className="px-4 py-2 font-mono text-xs">{dep.consumer_id}</td>
              <td className="px-4 py-2 text-xs">{dep.entity}</td>
              <td className="px-4 py-2 text-xs text-ink-2">{dep.policy}</td>
              <td className="px-4 py-2">
                {dep.paused ? (
                  <span className="inline-flex bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">paused</span>
                ) : (
                  <span className="inline-flex bg-ground px-2 py-0.5 text-xs text-ink-2">active</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
