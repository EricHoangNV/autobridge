/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}", "./lib/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: "var(--ground)",
        band: "var(--band)",
        ink: "var(--ink)",
        "ink-2": "var(--ink-2)",
        rule: "var(--rule)",
        off: "var(--off)",
        live: "var(--live)",
        "live-ink": "var(--live-ink)",
        stop: "var(--stop)",
        border: "hsl(var(--border))",
        muted: "hsl(var(--muted))",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "Segoe UI", "Roboto", "Arial", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
