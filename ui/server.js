// AutoBridge UI stub (W1). Serves /healthz + /readyz only.
// W7 replaces this with the Next.js 15 app (App Router, TypeScript,
// Tailwind, SWR polling + SSE). The stub exists so `make up` / `make smoke`
// cover the `ui` service and its network isolation (engine-net only:
// the UI never calls simulators or the verifier directly).

const http = require("node:http");

const PORT = Number(process.env.PORT || 3000);

const json = (res, code, obj) => {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "content-type": "application/json" });
  res.end(body);
};

const server = http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/healthz") {
    json(res, 200, { status: "ok" });
  } else if (req.method === "GET" && req.url === "/readyz") {
    json(res, 200, { status: "ok", checks: { default: "ok" } });
  } else if (req.method === "GET" && req.url === "/") {
    json(res, 200, { service: "autobridge-ui-stub", status: "ok" });
  } else {
    json(res, 404, { status: "not_found" });
  }
});

server.listen(PORT, () => {
  console.log(`ui-stub listening on :${PORT}`);
});
