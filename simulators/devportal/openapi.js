// Renders an OpenAPI 3.1 document (already parsed to JSON by the server) as an API
// reference: one section per operation with parameters, responses, a curl example and
// an example response synthesized from the schema; then the data models. Every string
// from the spec is escaped before it reaches the page.
(function () {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function resolve(spec, node) {
    let n = node || {};
    while (n && n.$ref) {
      const parts = n.$ref.replace(/^#\//, "").split("/");
      n = parts.reduce((acc, p) => (acc ? acc[p] : undefined), spec) || {};
    }
    if (Array.isArray(n.allOf)) {
      // Merge composed schemas (e.g. a shared envelope plus a typed `data`): later parts win.
      const merged = { type: "object", properties: {}, required: [] };
      for (const part of n.allOf) {
        const p = resolve(spec, part);
        Object.assign(merged.properties, p.properties || {});
        merged.required.push(...(p.required || []));
        if (p.description && !merged.description) merged.description = p.description;
      }
      return merged;
    }
    return n;
  }
  const refName = (node) => (node && node.$ref ? node.$ref.split("/").pop() : null);

  function typeLabel(spec, s) {
    if (!s) return "";
    const name = refName(s);
    if (name) return '<a class="model" href="#model-' + esc(name) + '">' + esc(name) + "</a>";
    if (s.type === "array") return "array of " + typeLabel(spec, s.items);
    const t = Array.isArray(s.type) ? s.type.join(" | ") : s.type || "object";
    return esc(t) + (s.format ? ' <span class="fmt">' + esc(s.format) + "</span>" : "");
  }

  // Realistic example values by field name, so example responses read like real data.
  const EXAMPLES = {
    property_id: "prop-001", propertyID: "prop-001", propertyName: "Grand Plaza",
    propertyTimezone: "America/New_York", name: "Grand Plaza", timezone: "America/New_York", currency: "USD",
    currencyCode: "USD", reservation_id: "RES-000042-prop-001-0000", reservationID: "RES-000042-prop-001-0000",
    guest_name: "Ada Lovelace", guestName: "Ada Lovelace", firstName: "Ada", lastName: "Lovelace",
    email: "ada.lovelace@example.com", guestEmail: "ada.lovelace@example.com",
    guestID: "G-RES-000042-prop-001-0000", charge_id: "CHG-000042-RES-000042-prop-001-0000-00",
    transactionID: "CHG-000042-RES-000042-prop-001-0000-00",
    startDate: "2026-10-26", endDate: "2026-10-28", dateCreated: "2026-10-26 08:00:00",
    dateModified: "2026-10-26 12:00:00", transactionDateTime: "2026-10-26 14:30:00",
    businessDate: "2026-10-26", serviceDate: "2026-10-26",
    adults: "2", children: "0", balance: 200.0, total: 200.0, amount: 100.0,
    description: "Room charge", sourceName: "Direct",
    code: "unauthorized", message: "Missing x-api-key header",
  };

  function sample(spec, s, depth, key) {
    s = resolve(spec, s);
    const t = Array.isArray(s.type) ? s.type.find((x) => x !== "null") : s.type;
    // Envelope counters are integers even where a money field shares the name (e.g. `total`).
    if (t === "integer" && key === "count") return 1;
    if (t === "integer" && key === "total") return 30;
    if (t === "boolean") return key === "success" ? true : false;
    if (key && Object.prototype.hasOwnProperty.call(EXAMPLES, key)) return EXAMPLES[key];
    if (depth > 6) return null;
    if (s.enum) return s.enum[0];
    if (t === "array") return [sample(spec, s.items, depth + 1)];
    if (t === "integer") return key === "total" ? 30 : key === "next_offset" || key === "limit" ? 10 : s.default !== undefined ? s.default : s.minimum || 0;
    if (t === "string") {
      if (s.format === "date") return "2026-10-05";
      if (s.format === "date-time") return "2026-10-06T14:12:00+00:00";
      if (s.pattern && s.pattern.indexOf("[0-9]{2}") >= 0) return "123.45";
      return "string";
    }
    if (t === "object" || s.properties) {
      const o = {};
      for (const [k, v] of Object.entries(s.properties || {})) o[k] = sample(spec, v, depth + 1, k);
      return o;
    }
    return null;
  }

  function fieldsTable(spec, schema) {
    const s = resolve(spec, schema);
    const req = new Set(s.required || []);
    const rows = Object.entries(s.properties || {}).map(([k, v]) => {
      const r = resolve(spec, v);
      const notes = [];
      if (r.description) notes.push(esc(r.description));
      if (r.enum) notes.push("One of " + r.enum.map((e) => "<code>" + esc(e) + "</code>").join(", "));
      if (r.pattern) notes.push("Pattern <code>" + esc(r.pattern) + "</code>");
      if (Array.isArray(r.type) && r.type.includes("null")) notes.push("May be <code>null</code>");
      return (
        "<tr><td><code>" + esc(k) + "</code>" + (req.has(k) ? '<span class="req">required</span>' : "") +
        "</td><td>" + typeLabel(spec, v) + "</td><td>" + (notes.join(". ") || "&nbsp;") + "</td></tr>"
      );
    });
    return rows.length ? '<table class="fields"><thead><tr><th>Field</th><th>Type</th><th>Notes</th></tr></thead><tbody>' + rows.join("") + "</tbody></table>" : "";
  }

  function render(spec) {
    const server = (spec.servers && spec.servers[0] && spec.servers[0].url) || "";
    const nav = [];
    const main = [];
    const ops = [];
    for (const [path, item] of Object.entries(spec.paths || {})) {
      for (const [method, op] of Object.entries(item)) ops.push({ path, method, op });
    }
    const isMeta = (o) => /^\/(healthz|readyz)$/.test(o.path);

    nav.push('<p class="nav-h">Endpoints</p>');
    for (const o of ops.filter((o) => !isMeta(o))) {
      const id = "op-" + (o.op.operationId || o.method + o.path);
      nav.push('<a href="#' + esc(id) + '"><span class="m m-' + o.method + '">' + o.method.toUpperCase() + "</span>" + esc(o.op.summary || o.path) + "</a>");
      const params = (o.op.parameters || []).map((p) => {
        const sc = p.schema || {};
        const bits = [];
        if (sc.default !== undefined) bits.push("default <code>" + esc(sc.default) + "</code>");
        if (sc.minimum !== undefined && sc.maximum !== undefined) bits.push("range " + esc(sc.minimum) + "–" + esc(sc.maximum));
        else if (sc.minimum !== undefined) bits.push("min " + esc(sc.minimum));
        else if (sc.maximum !== undefined) bits.push("max " + esc(sc.maximum));
        return "<tr><td><code>" + esc(p.name) + "</code>" + (p.required ? '<span class="req">required</span>' : "") + '</td><td><span class="in">' + esc(p.in) + "</span> " + typeLabel(spec, sc) + "</td><td>" + (bits.join(", ") || "&nbsp;") + "</td></tr>";
      });
      const responses = Object.entries(o.op.responses || {}).map(([code, r]) => {
        const rr = resolve(spec, r);
        const schema = rr.content && rr.content["application/json"] && rr.content["application/json"].schema;
        return '<div class="resp"><span class="code c' + esc(code[0]) + '">' + esc(code) + "</span> " + esc(rr.description || "") + (schema && code[0] === "2" ? fieldsTable(spec, schema) : "") + "</div>";
      });
      const ok = Object.entries(o.op.responses || {}).find(([c]) => c[0] === "2");
      const okSchema = ok && resolve(spec, ok[1]).content && resolve(spec, ok[1]).content["application/json"].schema;
      const names = (o.op.parameters || []).filter((p) => p.in === "query").map((p) => p.name);
      let query = "";
      if (names.includes("pageNumber") || names.includes("pageSize")) query = "?pageNumber=1&pageSize=10";
      else if (names.includes("limit")) query = "?limit=10";
      const schemes = (spec.components && spec.components.securitySchemes) || {};
      const hasApiKey = Object.values(schemes).some((s) => s && s.type === "apiKey");
      const hasBearer = Object.values(schemes).some((s) => s && s.type === "http" && s.scheme === "bearer");
      const authFlag = hasApiKey ? " \\\n  -H 'x-api-key: $ATLAS_API_KEY'" : hasBearer ? " \\\n  -H 'Authorization: Bearer $ATLAS_API_KEY'" : "";
      const curl = "curl " + server + o.path.replace(/\{(\w+)\}/g, "RES-000042-prop-001-0000") + query + authFlag;
      main.push(
        '<section class="op" id="' + esc(id) + '"><div class="op-doc">' +
          '<h2><span class="m m-' + o.method + '">' + o.method.toUpperCase() + '</span><span class="path">' + esc(o.path) + "</span></h2>" +
          '<p class="summary">' + esc(o.op.summary || "") + "</p>" +
          (o.op.description ? "<p>" + esc(o.op.description) + "</p>" : "") +
          (params.length ? '<h3>Parameters</h3><table class="fields"><thead><tr><th>Name</th><th>Type</th><th>Constraints</th></tr></thead><tbody>' + params.join("") + "</tbody></table>" : "") +
          "<h3>Responses</h3>" + responses.join("") +
        '</div><div class="op-code"><p class="code-h">Request</p><pre><code>' + esc(curl) + "</code></pre>" +
          (okSchema ? '<p class="code-h">Response ' + esc(ok[0]) + '</p><pre><code>' + esc(JSON.stringify(sample(spec, okSchema, 0), null, 2)) + "</code></pre>" : "") +
        "</div></section>"
      );
    }

    const schemas = (spec.components && spec.components.schemas) || {};
    nav.push('<p class="nav-h">Models</p>');
    for (const [name, s] of Object.entries(schemas)) {
      nav.push('<a href="#model-' + esc(name) + '">' + esc(name) + "</a>");
      main.push('<section class="model-sec" id="model-' + esc(name) + '"><h2>' + esc(name) + "</h2>" + (s.additionalProperties === false ? '<p class="note">No additional properties.</p>' : "") + fieldsTable(spec, s) + "</section>");
    }

    const meta = ops.filter(isMeta).map((o) => "<code>" + o.method.toUpperCase() + " " + esc(o.path) + "</code>").join(" and ");
    const schemes = Object.entries((spec.components && spec.components.securitySchemes) || {})
      .map(([n, s]) => "<li><code>" + esc(n) + "</code>: " + esc(s.type) + " " + esc(s.scheme || "") + (s.bearerFormat ? " (" + esc(s.bearerFormat) + ")" : "") + ". " + esc(s.description || "") + "</li>")
      .join("");
    return {
      nav: nav.join(""),
      intro:
        "<h1>" + esc((spec.info && spec.info.title) || "API") + '</h1><p class="lede">' + esc((spec.info && spec.info.description) || "") + "</p>" +
        '<dl class="facts"><div><dt>Version</dt><dd>' + esc((spec.info && spec.info.version) || "") + "</dd></div><div><dt>Base URL</dt><dd><code>" + esc(server) + "</code></dd></div><div><dt>Spec</dt><dd>OpenAPI " + esc(spec.openapi || "") + "</dd></div></dl>" +
        '<h2 id="auth">Authentication</h2><ul>' + schemes + "</ul>" + (meta ? "<p>Health checks " + meta + " need no token.</p>" : ""),
      main: main.join(""),
    };
  }

  window.VendorOpenAPI = { render };
})();
