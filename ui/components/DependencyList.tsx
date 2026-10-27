"use client";

import type { DependencyOut } from "@/lib/api/client";

type Props = {
  dependencies: DependencyOut[];
};

export function DependencyList({ dependencies }: Props) {
  if (!dependencies.length) {
    return (
      <div data-testid="dependency-list-empty" className="border border-rule px-4 py-6 text-sm text-ink-2">
        No dependency registrations. Consumers that depend on this connector will appear here.
      </div>
    );
  }

  return (
    <div data-testid="dependency-list">
      <table data-testid="dependency-table" className="ctab">
        <thead>
          <tr>
            <th>Consumer</th>
            <th>Entity</th>
            <th>Policy</th>
            <th>Paused</th>
          </tr>
        </thead>
        <tbody>
          {dependencies.map((dep) => (
            <tr key={dep.id} data-testid={`dependency-row-${dep.id}`}>
              <td className="font-mono text-xs">{dep.consumer_id}</td>
              <td className="text-sm">{dep.entity}</td>
              <td className="text-sm text-ink-2">{dep.policy}</td>
              <td>
                {dep.paused ? (
                  <span className="st st-deg">paused</span>
                ) : (
                  <span className="st st-ok">active</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
