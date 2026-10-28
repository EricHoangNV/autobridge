// Small Markdown renderer for the simulated vendor docs (headings, paragraphs, lists,
// blockquotes, fenced code, rules, inline code, bold, italic, links). Input is escaped
// first, so the source document can never inject markup.
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const slug = (s) => s.toLowerCase().replace(/<[^>]+>/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  function inline(s) {
    const codes = [];
    s = esc(s).replace(/`([^`]+)`/g, (_, c) => "\u0000" + (codes.push(c) - 1) + "\u0000");
    s = s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2">$1</a>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + codes[+i] + "</code>");
  }

  // Returns { html, toc: [{level, text, id}] }.
  function render(src) {
    const lines = src.replace(/\r\n/g, "\n").split("\n");
    const out = [];
    const toc = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^```(\w*)/);
      if (fence) {
        const body = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
        i++;
        out.push('<pre class="code" data-lang="' + (fence[1] || "text") + '"><code>' + esc(body.join("\n")) + "</code></pre>");
        continue;
      }
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        const level = h[1].length;
        const html = inline(h[2]);
        const id = slug(html);
        toc.push({ level, text: html.replace(/<[^>]+>/g, ""), id });
        out.push("<h" + level + ' id="' + id + '">' + html + "</h" + level + ">");
        i++;
        continue;
      }
      if (/^---+\s*$/.test(line)) {
        out.push("<hr>");
        i++;
        continue;
      }
      if (/^>\s?/.test(line)) {
        const body = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ""));
        out.push("<blockquote>" + render(body.join("\n")).html + "</blockquote>");
        continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          let item = lines[i++].replace(/^\s*[-*]\s+/, "");
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i])) item += " " + lines[i++].trim();
          items.push("<li>" + inline(item) + "</li>");
        }
        out.push("<ul>" + items.join("") + "</ul>");
        continue;
      }
      if (!line.trim()) {
        i++;
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|>|---+\s*$|\s*[-*]\s+)/.test(lines[i])) para.push(lines[i++]);
      out.push("<p>" + inline(para.join(" ")) + "</p>");
    }
    return { html: out.join("\n"), toc };
  }

  window.VendorMarkdown = { render, inline };
})();
