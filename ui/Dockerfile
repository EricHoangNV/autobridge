# UI image (W7.4): Next.js 15 App Router build.
# AR-2-5: base images digest-pinned; deps installed with `npm ci` from the
# committed package-lock.json (no floating `npm install` fallback).
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS builder
WORKDIR /srv/ui
COPY package.json package-lock.json ./
RUN npm ci --include=dev
COPY . .
# REQ-018: no NEXT_PUBLIC_API_URL — the browser calls same-origin /api/* and
# must never bake a control hostname (or token) into the .next/static bundle.
# Next.js evaluates rewrites() at BUILD time, so the server-only CONTROL_URL
# must be present as a build arg (compose passes http://control:8000; the
# default localhost:8101 suits plain `npm run dev` / local mode, where
# rewrites() is evaluated at dev-server start from runtime env instead).
ARG CONTROL_URL=http://localhost:8101
RUN npm run build

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293
WORKDIR /srv/ui
ENV NODE_ENV=production
COPY --from=builder /srv/ui/package.json ./package.json
COPY --from=builder /srv/ui/node_modules ./node_modules
COPY --from=builder /srv/ui/.next ./.next
COPY --from=builder /srv/ui/public ./public
COPY --from=builder /srv/ui/next.config.mjs ./next.config.mjs
EXPOSE 3000
CMD ["npm", "start"]
