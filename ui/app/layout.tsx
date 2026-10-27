import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutoBridge",
  description: "Generic Connect + Heal engine — control plane",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-zinc-900 antialiased">
        <header className="border-b bg-zinc-50">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
            <a href="/connectors" className="text-sm font-semibold tracking-tight">
              AutoBridge
            </a>
            <nav className="flex items-center gap-4 text-sm text-zinc-600">
              <a href="/connectors" className="hover:text-zinc-900 hover:underline">
                Connectors
              </a>
              <a
                href="/healthz"
                className="rounded border bg-white px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-50"
              >
                healthz
              </a>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
