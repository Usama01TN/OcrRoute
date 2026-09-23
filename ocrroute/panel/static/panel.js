/* Shared panel helpers: API calls through the session, toasts, dialogs, charts, live feed. */
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  async function api(method, path, body, opts = {}) {
    const headers = { "X-Requested-With": "OcrRoute" };
    let payload = body;
    if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    const res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("json") ? await res.json() : await res.text();
    if (!res.ok && !opts.raw) {
      const msg = (data && (data.error_message || data.detail)) ? (data.error_message || JSON.stringify(data.detail)) : res.statusText;
      throw Object.assign(new Error(msg), { status: res.status, data });
    }
    return data;
  }

  const T = (k) => (window.I18N && window.I18N[k]) || k;
  function toast(msg, bad = false) {
    msg = T(msg);
    let wrap = $("#toasts");
    if (!wrap) { wrap = document.createElement("div"); wrap.id = "toasts"; wrap.className = "toast-container position-fixed bottom-0 end-0 p-3"; document.body.appendChild(wrap); }
    const el = document.createElement("div");
    el.className = "toast align-items-center border-0 " + (bad ? "text-bg-danger" : "text-bg-dark");
    el.setAttribute("role", "status");
    el.innerHTML = `<div class="d-flex"><div class="toast-body">${msg}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
    wrap.appendChild(el);
    if (window.bootstrap) { const t = new bootstrap.Toast(el, { delay: bad ? 6000 : 3000 }); el.addEventListener("hidden.bs.toast", () => el.remove()); t.show(); }
    else { el.classList.add("show"); setTimeout(() => el.remove(), bad ? 6000 : 3000); }
  }

  function confirmDo(msg) { return window.confirm(msg); }

  function fmtMs(ms) { return ms == null ? "" : ms >= 1000 ? (ms / 1000).toFixed(2) + " s" : ms + " ms"; }
  function fmtCents(c) { return c == null ? "" : "$" + (c / 100).toFixed(4); }

  // ---- charts: palette derived from CSS tokens, every instance registered so a theme switch recolours it live
  const charts = [];
  function chartTheme() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n, d) => (cs.getPropertyValue(n).trim() || d);
    const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    return {
      dark, ink: v("--bs-secondary-color", "#64748B"), line: v("--bs-border-color", "#E2E8F0"), text: v("--bs-body-color", "#0F172A"),
      surface: v("--ocr-surface", "#FFFFFF"), font: v("--bs-font-sans-serif", "system-ui"),
      palette: dark ? ["#60A5FA", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#22D3EE", "#F472B6"]
                    : ["#2563EB", "#059669", "#D97706", "#DC2626", "#7C3AED", "#0891B2", "#DB2777"],
    };
  }
  // Colours are scriptable options evaluated at draw time: after a theme switch chart.update() repaints with the
  // new tokens. No option objects are replaced (Chart.js options are proxies).
  const T_ = () => chartTheme();
  const dsColor = (ctx) => { const d = ctx.chart.data.datasets[ctx.datasetIndex] || {}; return d._fixedColor || T_().palette[ctx.datasetIndex % 7]; };
  const axis = (showGrid) => ({ ticks: { color: () => T_().ink }, grid: { display: showGrid, color: () => T_().line }, border: { color: () => T_().line } });
  function chart(canvas, type, labels, datasets, extra = {}) {
    if (!window.Chart || !canvas) return null;
    datasets.forEach((d) => {
      if (typeof d.backgroundColor === "string" && !d._fixedColor) d._fixedColor = d.backgroundColor;
      d.borderColor = dsColor;
      d.backgroundColor = (ctx) => { const col = dsColor(ctx); return type === "line" ? col + "22" : type === "bar" ? col + "D9" : col; };
      d.pointBackgroundColor = dsColor; d.pointBorderColor = () => T_().surface; d.hoverBackgroundColor = dsColor;
      d.borderWidth = d.borderWidth ?? 2; d.tension = 0.35; d.pointRadius = 2.5;
      if (type === "bar") d.borderRadius = 6;
      if (type === "line") d.fill = true;
    });
    const scales = type === "doughnut" ? {} : { x: axis(false), y: Object.assign(axis(true), { beginAtZero: true }) };
    Object.entries(extra.scales || {}).forEach(([k, s]) => { const base = scales[k] || axis(true); scales[k] = Object.assign({}, base, s, { ticks: base.ticks, border: base.border, grid: Object.assign({}, base.grid, (s || {}).grid || {}, { color: base.grid.color }) }); });
    const options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: { duration: 500 } }, extra, {
      scales,
      plugins: {
        legend: { display: datasets.length > 1, labels: { color: () => T_().text, boxWidth: 10, usePointStyle: true } },
        tooltip: { backgroundColor: () => (T_().dark ? "#0F1724" : "#FFFFFF"), titleColor: () => T_().text, bodyColor: () => T_().text, borderColor: () => T_().line, borderWidth: 1, padding: 10, cornerRadius: 8 },
      },
    });
    Chart.defaults.font.family = T_().font;
    const c = new Chart(canvas, { type, data: { labels, datasets }, options });
    charts.push(c);
    return c;
  }
  function syncFavicon() {
    const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
    document.querySelectorAll("link[data-favicon]").forEach((l) => { l.href = dark ? "/panel/static/favicon-dark.svg" : "/panel/static/favicon.svg"; });
  }
  function repaintCharts() { syncFavicon(); charts.forEach((c) => c.update()); }

  function liveFeed(listEl, onRun) {
    if (!listEl) return;
    const es = new EventSource("/panel/stream");
    es.addEventListener("run", (e) => {
      const p = JSON.parse(e.data);
      const li = document.createElement("li");
      li.innerHTML = `<span class="muted">${(p.created_at || "").slice(11, 19)}</span><a href="/panel/runs/${p.id}">${p.route || "-"} · ${p.engine || "-"}</a><span class="num">${fmtMs(p.duration_ms)}</span><span class="status ${p.status}">${p.status}</span>`;
      listEl.prepend(li);
      while (listEl.children.length > 30) listEl.lastChild.remove();
      if (onRun) onRun(p);
    });
    es.onerror = () => { es.close(); setTimeout(() => liveFeed(listEl, onRun), 5000); };
  }

  // theme: light / dark / system (persisted per browser; system follows prefers-color-scheme)
  function applyTheme(mode) {
    const effective = mode === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : mode;
    document.documentElement.setAttribute("data-bs-theme", effective);
    document.documentElement.dataset.theme = mode;
    $$(".seg [data-theme]").forEach((b) => b.classList.toggle("active", b.dataset.theme === mode));
    try { localStorage.setItem("ocrroute-theme", mode); } catch {}
    requestAnimationFrame(repaintCharts);  // CSS tokens are updated; recolour every chart in place
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if ((document.documentElement.dataset.theme || "system") === "system") applyTheme("system"); });
  const savedTheme = (() => { try { return localStorage.getItem("ocrroute-theme") || "system"; } catch { return "system"; } })();
  applyTheme(savedTheme);
  $$(".seg [data-theme]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.theme)));
  const tt = $("#theme-toggle");
  if (tt) tt.addEventListener("click", () => {
    const effective = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "dark" : "light";
    applyTheme(effective === "dark" ? "light" : "dark");
  });

  // sidebar: collapsible groups (state per browser) + live filter
  $$(".ocr-group > button").forEach((btn) => {
    const g = btn.parentElement, key = "ocrroute-group-" + g.dataset.group;
    try { if (localStorage.getItem(key) === "0") g.classList.add("collapsed"); } catch {}
    btn.addEventListener("click", () => { g.classList.toggle("collapsed"); btn.setAttribute("aria-expanded", String(!g.classList.contains("collapsed"))); try { localStorage.setItem(key, g.classList.contains("collapsed") ? "0" : "1"); } catch {} });
  });
  $$("[data-nav-filter]").forEach((inp) => inp.addEventListener("input", () => {
    const q = inp.value.trim().toLowerCase();
    $$(".ocr-nav-item").forEach((a) => { a.style.display = !q || a.dataset.label.includes(q) ? "" : "none"; });
    $$(".ocr-group").forEach((g) => g.classList.toggle("collapsed", q ? ![...g.querySelectorAll(".ocr-nav-item")].some((a) => a.style.display !== "none") : (g.classList.contains("collapsed") && !q)));
  }));

  // server controls in the sidebar footer
  $$("[data-action='restart']").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmDo(T("Restart the server process") + "?")) return;
    b.disabled = true; b.innerHTML = '<i class="bi bi-arrow-repeat spin me-1"></i>' + T("Restart");
    try { const r = await api("POST", "/v1/endpoints/server/restart"); toast(T("Restarting…"));
      const t0 = Date.now(); const poll = async () => { try { const h = await fetch("/v1/health", {cache: "no-store"}); if (h.ok && Date.now() - t0 > 1500) { location.reload(); return; } } catch {} if (Date.now() - t0 < 60000) setTimeout(poll, 1000); else location.reload(); };
      setTimeout(poll, 1500); }
    catch (e) { toast(e.message, true); b.disabled = false; b.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>' + T("Restart"); }
  }));
  $$("[data-action='shutdown']").forEach((b) => b.addEventListener("click", async () => {
    if (!confirmDo(T("Stop the server") + "?")) return;
    try { await api("POST", "/v1/endpoints/server/shutdown"); toast(T("Shutting down…")); document.body.insertAdjacentHTML("beforeend", `<div class="position-fixed top-0 start-0 w-100 h-100 d-grid align-content-center text-center" style="background:var(--bs-body-bg);z-index:2000"><div><i class="bi bi-power" style="font-size:3rem;color:var(--ocr-bad)"></i><h2 class="mt-3">${T("Server stopped")}</h2><p class="text-muted">${T("Start it again with")} <code>ocrroute serve</code></p></div></div>`); }
    catch (e) { toast(e.message, true); }
  }));
  const qn = $("#quicknav"); if (qn) qn.addEventListener("click", () => { window.showDialog($("#palette")); setTimeout(() => $("#palette input")?.focus(), 150); });
  // engines-ready pill in the sidebar footer
  const ers = $$("[data-engines-ready]"); if (ers.length) api("GET", "/v1/ready").then((r) => ers.forEach((er) => { er.textContent = (r.status === "ready" ? "● " : "○ ") + er.textContent; })).catch(() => {});

  // command palette (Ctrl/⌘+K) & search focus (/)
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); window.showDialog($("#palette")); setTimeout(() => $("#palette input")?.focus(), 150); }
    if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) { e.preventDefault(); ($(".ocr-sidebar.d-lg-flex .global-search") || $(".global-search") || $("#global-search"))?.focus(); }
  });
  const pal = $("#palette");
  if (pal) {
    const input = $("input", pal), list = $("ul", pal);
    const items = $$(".ocr-nav-item").map((a) => ({ label: a.querySelector(".t")?.textContent.trim() || a.textContent.trim(), href: a.href }));
    const renderList = (q = "") => { list.innerHTML = ""; items.filter((i) => i.label.toLowerCase().includes(q.toLowerCase())).forEach((i) => { const li = document.createElement("li"); li.innerHTML = `<a class="dropdown-item rounded-2" href="${i.href}"><i class="bi bi-arrow-return-right me-2 text-muted"></i>${i.label}</a>`; list.appendChild(li); }); };
    input.addEventListener("input", () => renderList(input.value)); renderList();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { const a = $("a", list); if (a) location.href = a.href; } });
  }

    window.showDialog = (el) => { if (!el) return; if (el.tagName === "DIALOG") el.showModal(); else bootstrap.Modal.getOrCreateInstance(el).show(); };
  window.hideDialog = (el) => { if (!el) return; if (el.tagName === "DIALOG") el.close(); else bootstrap.Modal.getOrCreateInstance(el).hide(); };
  window.OcrRoute = { api, toast, confirmDo, fmtMs, fmtCents, chart, liveFeed, repaintCharts, $, $$ };
})();
