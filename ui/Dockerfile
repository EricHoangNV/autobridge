# UI stub image (W1). W7 replaces the stub with the Next.js 15 build.
# The UI lives on engine-net only and talks to control alone.
FROM node:20-alpine

WORKDIR /srv/ui

COPY package.json server.js ./

EXPOSE 3000

CMD ["node", "server.js"]
