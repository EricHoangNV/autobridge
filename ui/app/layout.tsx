import type { Metadata } from "next";
import { Archivo, Fragment_Mono } from "next/font/google";
import "./globals.css";
import { TopBar } from "@/components/TopBar";

const archivo = Archivo({
  subsets: ["latin"],
  axes: ["wdth"],
  variable: "--font-sans",
});

const fragmentMono = Fragment_Mono({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "AutoBridge",
  description: "Generic Connect + Heal engine — control plane",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        className={`${archivo.variable} ${fragmentMono.variable} min-h-screen bg-ground font-sans text-ink antialiased`}
      >
        <TopBar />
        <main className="mx-auto max-w-6xl px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
