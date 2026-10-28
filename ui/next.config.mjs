/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // Server-only env (REQ-018): the browser never sees this value — it only
    // calls same-origin /api/*. Compose sets CONTROL_URL=http://control:8000;
    // plain `npm run dev` / local mode (REQ-017) defaults to localhost:8101.
    const apiUrl = process.env.CONTROL_URL || "http://localhost:8101";
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/:path*`,
      },
    ];
  },
};
export default nextConfig;
