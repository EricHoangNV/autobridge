# UI image (W7.4): Next.js 15 App Router build.
FROM node:20-alpine AS builder
WORKDIR /srv/ui
COPY package.json package-lock.json* pnpm-lock.yaml* ./
RUN npm install --include=dev || npm install --include=dev --legacy-peer-deps
COPY . .
ARG NEXT_PUBLIC_API_URL=http://control:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:20-alpine
WORKDIR /srv/ui
ENV NODE_ENV=production
COPY --from=builder /srv/ui/package.json ./package.json
COPY --from=builder /srv/ui/node_modules ./node_modules
COPY --from=builder /srv/ui/.next ./.next
COPY --from=builder /srv/ui/public ./public
COPY --from=builder /srv/ui/next.config.mjs ./next.config.mjs
EXPOSE 3000
CMD ["npm", "start"]
