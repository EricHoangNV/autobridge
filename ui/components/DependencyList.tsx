"use client";

import type { DependencyOut } from "@/lib/api/client";

type Props = {
  dependencies: DependencyOut[];
};

export function DependencyList({ dependencies }: Props) {
  if (!dependencies.length) {
    return (
      <div data-testid="dependency-list-empty" className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-zinc-500">
        No dependency registrations. Consumers that depend on this connector will appear here.
      </div>
    );
  }

  return (
    <div data-testid="dependency-list" className="overflow-hidden rounded-lg border bg-white">
      <table data-testid="dependency-table" className="w-full text-left text-sm">
        <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="px-4 py-2 font-medium">Consumer</th>
            <th className="px-4 py-2 font-medium">Entity</th>
            <th className="px-4 py-2 font-medium">Policy</th>
            <th className="px-4 py-2 font-medium">Paused</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {dependencies.map((dep) => (
            <tr key={dep.id} data-testid={`dependency-row-${dep.id}`} className="hover:bg-zinc-50">
              <td className="px-4 py-2 font-mono text-xs">{dep.consumer_id}</td>
              <td className="px-4 py-2 text-xs">{dep.entity}</td>
              <td className="px-4 py-2 text-xs text-zinc-600">{dep.policy}</td>
              <td className="px-4 py-2">
                {dep.paused ? (
                  <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">paused</span>
                ) : (
                  <span className="inline-flex rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600">active</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
