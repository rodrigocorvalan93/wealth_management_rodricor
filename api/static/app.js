/* === WM PWA — Single-page app === */
(function () {
  "use strict";

  const TOKEN_KEY = "wm_api_token";
  const ANCHOR_KEY = "wm_anchor";
  const VIEW_KEY = "wm_view";

  // -------- API client --------
  const API = {
    base: window.location.origin,
    token: () => localStorage.getItem(TOKEN_KEY) || "",
    anchor: () => localStorage.getItem(ANCHOR_KEY) || "USD",
    view: () => localStorage.getItem(VIEW_KEY) || "all",
    setToken: (t) => localStorage.setItem(TOKEN_KEY, t),
    setAnchor: (a) => localStorage.setItem(ANCHOR_KEY, a),
    setView: (v) => localStorage.setItem(VIEW_KEY, v),
    logout: () => {
      localStorage.removeItem(TOKEN_KEY);
      window.location.hash = "";
      window.location.reload();
    },

    async req(path, opts = {}) {
      const url = `${this.base}${path}`;
      const headers = {
        ...(opts.headers || {}),
      };
      if (!opts.skipAuth) {
        headers["Authorization"] = `Bearer ${this.token()}`;
      }
      if (opts.json !== undefined) {
        headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(opts.json);
      }
      let res;
      try {
        res = await fetch(url, { ...opts, headers });
      } catch (err) {
        throw new Error("Sin conexión");
      }
      if (res.status === 401) {
        toast("Token inválido — re-loginearse", "error");
        setTimeout(() => API.logout(), 800);
        throw new Error("401 unauthorized");
      }
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          msg = body.message || msg;
        } catch (_) {}
        throw new Error(msg);
      }
      const ct = res.headers.get("content-type") || "";
      return ct.includes("application/json") ? res.json() : res.text();
    },

    // Endpoints específicos
    health: () => API.req("/api/health", { skipAuth: true }),
    summary: (anchor) => API.req(`/api/summary?anchor=${anchor || API.anchor()}`),
    holdings: (anchor) => API.req(`/api/holdings?anchor=${anchor || API.anchor()}`),
    tradeStats: (anchor) => API.req(`/api/trade-stats?anchor=${anchor || API.anchor()}`),
    buyingPower: (anchor) => API.req(`/api/buying-power?anchor=${anchor || API.anchor()}`),
    realizedPnl: () => API.req(`/api/realized-pnl`),
    equityCurve: (anchor, investible) => API.req(
      `/api/equity-curve?anchor=${anchor || API.anchor()}&investible=${!!investible}`
    ),
    refresh: () => API.req("/api/refresh", { method: "POST" }),
    backups: () => API.req("/api/backups"),

    listSheet: (sheet) => API.req(`/api/sheets/${sheet}`),
    getSheetRow: (sheet, id) => API.req(`/api/sheets/${sheet}/${id}`),
    createRow: (sheet, data) => API.req(`/api/sheets/${sheet}`, { method: "POST", json: data }),
    updateRow: (sheet, id, data) => API.req(`/api/sheets/${sheet}/${id}`, { method: "PUT", json: data }),
    deleteRow: (sheet, id) => API.req(`/api/sheets/${sheet}/${id}`, { method: "DELETE" }),
  };

  // -------- Toast --------
  function toast(msg, type = "info") {
    const t = document.createElement("div");
    t.className = `toast ${type}`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), type === "error" ? 4000 : 2200);
  }

  // -------- Utils --------
  const fmt = {
    money: (v, dec = 2) => (v === null || v === undefined || isNaN(v))
      ? "—"
      : Number(v).toLocaleString("es-AR", {
          minimumFractionDigits: dec, maximumFractionDigits: dec,
        }),
    pct: (v) => v === null || v === undefined ? "—" : (v * 100).toFixed(2) + "%",
    date: (s) => s ? s.slice(0, 10) : "",
    today: () => new Date().toISOString().slice(0, 10),
  };

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[c]));
  }

  // -------- Cache de cuentas/especies/monedas --------
  let _meta = null;
  async function loadMeta() {
    if (_meta) return _meta;
    // Por simplicidad, traemos todo via summary (devuelve breakdowns por cuenta).
    // Para selects necesitamos lista completa de cuentas/tickers/monedas.
    // Usamos endpoint /api/holdings (incluye account/asset/native_currency).
    try {
      const [s, h] = await Promise.all([API.summary(), API.holdings()]);
      const accounts = Array.from(new Set(h.items.map(x => x.account))).sort();
      const tickers = Array.from(new Set(
        h.items.filter(x => !x.is_cash).map(x => x.asset)
      )).sort();
      const currencies = Array.from(new Set(h.items.map(x => x.native_currency))).sort();
      _meta = { accounts, tickers, currencies, summary: s };
      return _meta;
    } catch (e) {
      return { accounts: [], tickers: [], currencies: [], summary: null };
    }
  }
  function invalidateMeta() { _meta = null; }

  // -------- Router --------
  const routes = {};
  function route(path, handler) { routes[path] = handler; }
  function navigate(path) { window.location.hash = path; }

  function matchRoute(hash) {
    const path = hash.replace(/^#/, "") || "/";
    // Exact match first
    if (routes[path]) return { handler: routes[path], params: {} };
    // Try patterns
    for (const pattern of Object.keys(routes)) {
      const re = new RegExp("^" + pattern.replace(/:(\w+)/g, "([^/]+)") + "$");
      const m = path.match(re);
      if (m) {
        const keys = (pattern.match(/:(\w+)/g) || []).map(k => k.slice(1));
        const params = {};
        keys.forEach((k, i) => params[k] = m[i + 1]);
        return { handler: routes[pattern], params };
      }
    }
    return { handler: routes["/"], params: {} };
  }

  async function render() {
    const { handler, params } = matchRoute(window.location.hash);
    const root = document.getElementById("root");
    root.innerHTML = '<div class="loading"><div class="spinner"></div>Cargando...</div>';
    try {
      const html = await handler(params);
      root.innerHTML = html;
      attachListeners(root);
      updateNav();
    } catch (e) {
      root.innerHTML = `<div class="empty"><div class="icon">⚠️</div><div>${escapeHtml(e.message)}</div></div>`;
    }
  }

  function updateNav() {
    const path = window.location.hash.replace(/^#/, "") || "/";
    document.querySelectorAll(".bottom-nav a").forEach(a => {
      const target = a.getAttribute("href").replace(/^#/, "");
      a.classList.toggle("active", path === target ||
        (target !== "/" && path.startsWith(target)));
    });
    // FAB visibility
    const fab = document.getElementById("fab");
    if (!fab) return;
    const fabRoutes = {
      "/trades": "#/trades/new",
      "/gastos": "#/gastos/new",
      "/ingresos": "#/ingresos/new",
      "/transferencias": "#/transferencias/new",
    };
    let fabHref = null;
    for (const [k, v] of Object.entries(fabRoutes)) {
      if (path === k || path.startsWith(k + "/")) { fabHref = v; break; }
    }
    if (fabHref && !path.endsWith("/new") && !path.endsWith("/edit")) {
      fab.style.display = "flex";
      fab.setAttribute("href", fabHref);
    } else {
      fab.style.display = "none";
    }
  }

  function attachListeners(root) {
    // Forms con data-action
    root.querySelectorAll("form[data-action]").forEach(f => {
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const action = f.dataset.action;
        const data = Object.fromEntries(new FormData(f).entries());
        // Coercer empty strings → null
        for (const k of Object.keys(data)) {
          if (data[k] === "") data[k] = null;
        }
        const submit = f.querySelector("button[type=submit]");
        if (submit) submit.disabled = true;
        try {
          await window._actions[action](data, f);
        } catch (err) {
          toast(err.message, "error");
        } finally {
          if (submit) submit.disabled = false;
        }
      });
    });
    // Botones data-onclick
    root.querySelectorAll("[data-onclick]").forEach(b => {
      b.addEventListener("click", (e) => {
        const fn = b.dataset.onclick;
        const arg = b.dataset.arg;
        if (window._actions[fn]) window._actions[fn](arg, b);
      });
    });
  }

  // -------- Actions --------
  window._actions = {
    async createTrade(data) {
      // Convertir tipos numéricos
      ["Qty", "Precio", "Comisión"].forEach(k => {
        if (data[k] !== null) data[k] = parseFloat(data[k]);
      });
      await API.createRow("blotter", data);
      invalidateMeta();
      toast("Trade agregado ✓", "success");
      navigate("/trades");
    },
    async updateTrade(data, form) {
      ["Qty", "Precio", "Comisión"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined) data[k] = parseFloat(data[k]);
      });
      const id = form.dataset.rowId;
      await API.updateRow("blotter", id, data);
      invalidateMeta();
      toast("Trade actualizado ✓", "success");
      navigate("/trades");
    },
    async deleteTrade(id) {
      if (!confirm(`¿Borrar trade ${id}?`)) return;
      await API.deleteRow("blotter", id);
      invalidateMeta();
      toast("Trade borrado", "success");
      render();
    },
    async createGasto(data) {
      ["Monto", "Cuotas"].forEach(k => {
        if (data[k] !== null) data[k] = parseFloat(data[k]);
      });
      await API.createRow("gastos", data);
      invalidateMeta();
      toast("Gasto agregado ✓", "success");
      navigate("/gastos");
    },
    async updateGasto(data, form) {
      ["Monto", "Cuotas"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined) data[k] = parseFloat(data[k]);
      });
      const id = form.dataset.rowId;
      await API.updateRow("gastos", id, data);
      invalidateMeta();
      toast("Gasto actualizado ✓", "success");
      navigate("/gastos");
    },
    async deleteGasto(id) {
      if (!confirm(`¿Borrar gasto ${id}?`)) return;
      await API.deleteRow("gastos", id);
      invalidateMeta();
      toast("Gasto borrado", "success");
      render();
    },
    async createIngreso(data) {
      if (data.Monto !== null) data.Monto = parseFloat(data.Monto);
      await API.createRow("ingresos", data);
      invalidateMeta();
      toast("Ingreso agregado ✓", "success");
      navigate("/ingresos");
    },
    async updateIngreso(data, form) {
      if (data.Monto !== null && data.Monto !== undefined) data.Monto = parseFloat(data.Monto);
      const id = form.dataset.rowId;
      await API.updateRow("ingresos", id, data);
      invalidateMeta();
      toast("Ingreso actualizado ✓", "success");
      navigate("/ingresos");
    },
    async deleteIngreso(id) {
      if (!confirm(`¿Borrar ingreso ${id}?`)) return;
      await API.deleteRow("ingresos", id);
      invalidateMeta();
      toast("Ingreso borrado", "success");
      render();
    },
    async createTransfer(data) {
      if (data.Monto !== null) data.Monto = parseFloat(data.Monto);
      await API.createRow("transferencias_cash", data);
      invalidateMeta();
      toast("Transferencia agregada ✓", "success");
      navigate("/transferencias");
    },
    async deleteTransfer(id) {
      if (!confirm(`¿Borrar transferencia ${id}?`)) return;
      await API.deleteRow("transferencias_cash", id);
      invalidateMeta();
      toast("Transferencia borrada", "success");
      render();
    },
    async refreshAll() {
      try {
        toast("Refrescando...", "info");
        await API.refresh();
        invalidateMeta();
        toast("Refresh completado ✓", "success");
        render();
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    setView(v) {
      API.setView(v);
      render();
    },
    setAnchor(a) {
      API.setAnchor(a);
      render();
    },
    logout() {
      if (confirm("¿Cerrar sesión y borrar token?")) {
        API.logout();
      }
    },
  };

  // -------- Pages --------

  // /  Dashboard
  route("/", async () => {
    const view = API.view();  // 'all' | 'investible'
    const [s, fills] = await Promise.all([
      API.summary(), API.realizedPnl(),
    ]);

    const pn = view === "investible" ? s.patrimonio_invertible : s.patrimonio_total;
    const totalNet = pn;

    // Top cuentas (filtradas: solo activos)
    const acc = Object.entries(s.by_account)
      .filter(([_, v]) => v > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);
    const totalAcc = acc.reduce((a, [_, v]) => a + Math.abs(v), 0) || 1;

    // Por asset class
    const cls = Object.entries(s.by_asset_class).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));

    // Últimos PnL realizado
    const recentFills = (fills.fills || []).slice(0, 5);

    return `
      <div class="topbar">
        <h1>📊 Portfolio</h1>
        <div class="actions">
          <button data-onclick="refreshAll" title="Refrescar">⟳</button>
        </div>
      </div>
      <main>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
          <div class="toggle-pill">
            <button data-onclick="setView" data-arg="all" class="${view === "all" ? "active" : ""}">📦 Todo</button>
            <button data-onclick="setView" data-arg="investible" class="${view === "investible" ? "active" : ""}">💎 Invertible</button>
          </div>
          <div class="toggle-pill">
            ${["USD", "USB", "ARS"].map(a =>
              `<button data-onclick="setAnchor" data-arg="${a}" class="${API.anchor() === a ? "active" : ""}">${a}</button>`
            ).join("")}
          </div>
        </div>

        <div class="kpi-grid three">
          <div class="kpi primary">
            <div class="kpi-label">Patrimonio Neto ${view === "investible" ? "(invertible)" : "(total)"}</div>
            <div class="kpi-value">${fmt.money(totalNet)}</div>
            <div class="kpi-currency">${API.anchor()}</div>
          </div>
        </div>

        <div class="kpi-grid">
          <div class="kpi">
            <div class="kpi-label">📈 Activos</div>
            <div class="kpi-value positive">${fmt.money(s.patrimonio_total + (s.patrimonio_total < 0 ? 0 : 0))}</div>
            <div class="kpi-currency">${API.anchor()}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">📉 No-Invertible</div>
            <div class="kpi-value">${fmt.money(s.patrimonio_no_invertible || 0)}</div>
            <div class="kpi-currency">cash de reserva</div>
          </div>
        </div>

        <section>
          <h2>Por asset class</h2>
          <div class="card">
            ${cls.length === 0 ? '<div class="muted">Sin posiciones</div>' :
              cls.map(([k, v]) => {
                const pct = (Math.abs(v) / Math.abs(totalNet)) * 100;
                return `<div style="display:flex; justify-content:space-between; padding: 6px 0; border-bottom: 1px solid var(--border);">
                  <span>${escapeHtml(k)}</span>
                  <span class="tabular ${v < 0 ? 'negative' : ''}">${fmt.money(v)} <span class="muted">${pct.toFixed(1)}%</span></span>
                </div>`;
              }).join("")
            }
          </div>
        </section>

        <section>
          <h2>Top cuentas</h2>
          <div class="card">
            ${acc.length === 0 ? '<div class="muted">Sin cuentas</div>' :
              acc.map(([k, v]) => {
                const pct = (Math.abs(v) / totalAcc) * 100;
                return `<div style="display:flex; justify-content:space-between; padding: 6px 0; border-bottom: 1px solid var(--border);">
                  <span>${escapeHtml(k)}</span>
                  <span class="tabular ${v < 0 ? 'negative' : ''}">${fmt.money(v)} <span class="muted">${pct.toFixed(1)}%</span></span>
                </div>`;
              }).join("")
            }
          </div>
        </section>

        <section>
          <h2>PnL realizado reciente</h2>
          <div class="card">
            ${recentFills.length === 0 ? '<div class="muted">Sin trades cerrados</div>' :
              recentFills.map(f => `
                <div style="display:flex; justify-content:space-between; padding: 6px 0; border-bottom: 1px solid var(--border);">
                  <span>${escapeHtml(f.asset)} <span class="muted">${escapeHtml(fmt.date(f.fecha_venta))}</span></span>
                  <span class="tabular ${f.pnl_realizado > 0 ? 'positive' : 'negative'}">${fmt.money(f.pnl_realizado)} ${escapeHtml(f.currency)}</span>
                </div>
              `).join("")
            }
          </div>
        </section>

        <section>
          <a href="${API.base}/api/report/html?anchor=${API.anchor()}"
             target="_blank" rel="noopener"
             onclick="event.preventDefault(); openFullReport();"
             class="btn primary full">📄 Ver reporte completo</a>
        </section>
      </main>
    `;
  });

  // Helper: abrir el reporte HTML completo en nueva ventana, con auth
  window.openFullReport = async function () {
    try {
      toast("Cargando reporte...", "info");
      const html = await API.req(`/api/report/html?anchor=${API.anchor()}`);
      const blob = new Blob([html], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
    } catch (e) {
      toast(e.message, "error");
    }
  };

  // /trades — listar
  route("/trades", async () => {
    const data = await API.listSheet("blotter");
    const items = (data.items || [])
      .filter(r => r["Trade ID"] || r["Ticker"])
      .reverse();
    return `
      ${headerWithBack("📈 Trades", "/")}
      <main>
        ${items.length === 0 ? emptyState("Aún no cargaste trades", "Tocá el + para agregar uno") :
          `<div class="list">${items.map(r => `
            <a class="list-item" href="#/trades/${encodeURIComponent(r.row_id || "")}/edit">
              <div class="meta">
                <div class="meta-line1">
                  ${r.Side === "BUY" ? "🟢" : "🔴"} ${escapeHtml(r.Side || "")} ${escapeHtml(r.Ticker || "")}
                </div>
                <div class="meta-line2">
                  ${escapeHtml(fmt.date(r["Trade Date"]))} · ${escapeHtml(r.Cuenta || "")}
                  ${r["Trade ID"] ? "· " + escapeHtml(r["Trade ID"]) : ""}
                </div>
              </div>
              <div class="right">
                <div class="amount">${fmt.money(r.Qty, 0)}</div>
                <div class="sub">@ ${fmt.money(r.Precio, 4)} ${escapeHtml(r["Moneda Trade"] || "")}</div>
              </div>
            </a>
          `).join("")}</div>`
        }
      </main>
    `;
  });

  // /trades/new
  route("/trades/new", async () => {
    const meta = await loadMeta();
    return `
      ${headerWithBack("Nuevo trade", "/trades")}
      <main>
        <form data-action="createTrade">
          ${tradeFormFields({}, meta)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  // /trades/:id/edit
  route("/trades/:id/edit", async ({ id }) => {
    const meta = await loadMeta();
    const row = await API.getSheetRow("blotter", id);
    return `
      ${headerWithBack("Editar trade", "/trades")}
      <main>
        <form data-action="updateTrade" data-row-id="${escapeHtml(id)}">
          ${tradeFormFields(row, meta)}
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteTrade" data-arg="${escapeHtml(id)}">
          🗑 Borrar trade
        </button>
      </main>
    `;
  });

  // /gastos
  route("/gastos", async () => {
    const data = await API.listSheet("gastos");
    const items = (data.items || []).filter(r => r.Concepto || r.Monto).reverse();
    return `
      ${headerWithBack("💸 Gastos", "/")}
      <main>
        ${items.length === 0 ? emptyState("Sin gastos", "Tocá + para agregar") :
          `<div class="list">${items.map(r => `
            <a class="list-item" href="#/gastos/${encodeURIComponent(r.row_id || "")}/edit">
              <div class="meta">
                <div class="meta-line1">${escapeHtml(r.Concepto || "(sin concepto)")}</div>
                <div class="meta-line2">
                  ${escapeHtml(fmt.date(r.Fecha))} · ${escapeHtml(r.Categoría || r.Categoria || "—")}
                  ${r.Tipo ? " · " + escapeHtml(r.Tipo) : ""}
                </div>
              </div>
              <div class="right">
                <div class="amount negative">- ${fmt.money(r.Monto)}</div>
                <div class="sub">${escapeHtml(r.Moneda || "")} · ${escapeHtml(r["Cuenta Destino"] || "")}</div>
              </div>
            </a>
          `).join("")}</div>`
        }
      </main>
    `;
  });

  route("/gastos/new", async () => {
    const meta = await loadMeta();
    return `
      ${headerWithBack("Nuevo gasto", "/gastos")}
      <main>
        <form data-action="createGasto">
          ${gastoFormFields({}, meta)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  route("/gastos/:id/edit", async ({ id }) => {
    const meta = await loadMeta();
    const row = await API.getSheetRow("gastos", id);
    return `
      ${headerWithBack("Editar gasto", "/gastos")}
      <main>
        <form data-action="updateGasto" data-row-id="${escapeHtml(id)}">
          ${gastoFormFields(row, meta)}
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteGasto" data-arg="${escapeHtml(id)}">🗑 Borrar</button>
      </main>
    `;
  });

  // /ingresos
  route("/ingresos", async () => {
    const data = await API.listSheet("ingresos");
    const items = (data.items || []).filter(r => r.Concepto || r.Monto).reverse();
    return `
      ${headerWithBack("💰 Ingresos", "/")}
      <main>
        ${items.length === 0 ? emptyState("Sin ingresos", "Tocá + para agregar") :
          `<div class="list">${items.map(r => `
            <a class="list-item" href="#/ingresos/${encodeURIComponent(r.row_id || "")}/edit">
              <div class="meta">
                <div class="meta-line1">${escapeHtml(r.Concepto || "(sin concepto)")}</div>
                <div class="meta-line2">
                  ${escapeHtml(fmt.date(r.Fecha))} · ${escapeHtml(r.Categoría || r.Categoria || "—")}
                </div>
              </div>
              <div class="right">
                <div class="amount positive">+ ${fmt.money(r.Monto)}</div>
                <div class="sub">${escapeHtml(r.Moneda || "")} · ${escapeHtml(r["Cuenta Destino"] || "")}</div>
              </div>
            </a>
          `).join("")}</div>`
        }
      </main>
    `;
  });

  route("/ingresos/new", async () => {
    const meta = await loadMeta();
    return `
      ${headerWithBack("Nuevo ingreso", "/ingresos")}
      <main>
        <form data-action="createIngreso">
          ${ingresoFormFields({}, meta)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  route("/ingresos/:id/edit", async ({ id }) => {
    const meta = await loadMeta();
    const row = await API.getSheetRow("ingresos", id);
    return `
      ${headerWithBack("Editar ingreso", "/ingresos")}
      <main>
        <form data-action="updateIngreso" data-row-id="${escapeHtml(id)}">
          ${ingresoFormFields(row, meta)}
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteIngreso" data-arg="${escapeHtml(id)}">🗑 Borrar</button>
      </main>
    `;
  });

  // /transferencias
  route("/transferencias", async () => {
    const data = await API.listSheet("transferencias_cash");
    const items = (data.items || []).filter(r => r.Monto).reverse();
    return `
      ${headerWithBack("🔄 Transferencias", "/")}
      <main>
        ${items.length === 0 ? emptyState("Sin transferencias", "Tocá + para agregar") :
          `<div class="list">${items.map(r => `
            <div class="list-item">
              <div class="meta">
                <div class="meta-line1">${escapeHtml(r["Cuenta Origen"] || "")} → ${escapeHtml(r["Cuenta Destino"] || "")}</div>
                <div class="meta-line2">${escapeHtml(fmt.date(r.Fecha))} · ${escapeHtml(r.Description || "")}</div>
              </div>
              <div class="right">
                <div class="amount">${fmt.money(r.Monto)}</div>
                <div class="sub">${escapeHtml(r.Moneda || "")}</div>
              </div>
            </div>
          `).join("")}</div>`
        }
      </main>
    `;
  });

  route("/transferencias/new", async () => {
    const meta = await loadMeta();
    return `
      ${headerWithBack("Nueva transferencia", "/transferencias")}
      <main>
        <form data-action="createTransfer">
          ${transferFormFields({}, meta)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  // /pasivos
  route("/pasivos", async () => {
    const h = await API.holdings();
    const liabs = h.items.filter(x => x.is_liability && Math.abs(x.mv_anchor || 0) > 0.01);
    return `
      ${headerWithBack("📉 Pasivos", "/")}
      <main>
        <div class="card">
          <div class="muted" style="font-size:13px;">
            Cauciones tomadas, tarjetas de crédito y préstamos. Cada uno resta del PN.
          </div>
        </div>
        ${liabs.length === 0 ? emptyState("Sin pasivos pendientes", "🎉") :
          `<div class="list">${liabs.map(x => `
            <div class="list-item">
              <div class="meta">
                <div class="meta-line1">${escapeHtml(x.account)}</div>
                <div class="meta-line2">${escapeHtml(x.account_kind || "")} · ${escapeHtml(x.cash_purpose || "")}</div>
              </div>
              <div class="right">
                <div class="amount negative">${fmt.money(x.mv_anchor)}</div>
                <div class="sub">${escapeHtml(API.anchor())}</div>
              </div>
            </div>
          `).join("")}</div>`
        }
      </main>
    `;
  });

  // /performance
  route("/performance", async () => {
    const [stats, ec] = await Promise.all([
      API.tradeStats(),
      API.equityCurve(API.anchor(), false),
    ]);
    const byCcy = stats.by_currency || {};
    const m = ec.metrics || {};
    return `
      ${headerWithBack("🎯 Performance", "/")}
      <main>
        <section>
          <h2>Equity curve metrics</h2>
          <div class="kpi-grid">
            <div class="kpi">
              <div class="kpi-label">PN inicial</div>
              <div class="kpi-value">${fmt.money(m.first_value)}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">PN actual</div>
              <div class="kpi-value">${fmt.money(m.last_value)}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Retorno</div>
              <div class="kpi-value ${m.total_return_pct > 0 ? 'positive' : 'negative'}">${fmt.pct(m.total_return_pct)}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Max drawdown</div>
              <div class="kpi-value negative">${fmt.pct(m.max_drawdown_pct)}</div>
            </div>
            ${m.sharpe_ratio !== undefined && m.sharpe_ratio !== null ? `
            <div class="kpi">
              <div class="kpi-label">Sharpe</div>
              <div class="kpi-value">${fmt.money(m.sharpe_ratio, 2)}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Sortino</div>
              <div class="kpi-value">${fmt.money(m.sortino_ratio, 2)}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Calmar</div>
              <div class="kpi-value">${fmt.money(m.calmar_ratio, 2)}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Vol anualizada</div>
              <div class="kpi-value">${fmt.pct(m.volatility_annual)}</div>
            </div>
            ` : ""}
          </div>
        </section>

        <section>
          <h2>Trades por moneda</h2>
          ${Object.keys(byCcy).length === 0 ? `<div class="card muted">Sin trades cerrados aún</div>` :
            Object.entries(byCcy).map(([ccy, s]) => `
              <div class="card">
                <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                  <strong>${escapeHtml(ccy)}</strong>
                  <span class="tabular">${s.n_trades} trades</span>
                </div>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px; font-size:13px;">
                  <div>✅ Win: <b>${s.n_winners}</b></div>
                  <div>❌ Loss: <b>${s.n_losers}</b></div>
                  <div>Winrate: <b>${(s.winrate * 100).toFixed(1)}%</b></div>
                  <div>PF: <b>${s.profit_factor !== null ? s.profit_factor.toFixed(2) : "∞"}</b></div>
                  <div>Net PnL: <b class="${s.net_pnl > 0 ? 'positive' : 'negative'}">${fmt.money(s.net_pnl)}</b></div>
                  <div>Expect: <b>${fmt.money(s.expectancy)}</b></div>
                </div>
              </div>
            `).join("")
          }
        </section>
      </main>
    `;
  });

  // /settings
  route("/settings", async () => {
    let health = null;
    try { health = await API.health(); } catch (_) {}
    let backups = [];
    try {
      const b = await API.backups();
      backups = b.backups || [];
    } catch (_) {}

    return `
      ${headerWithBack("⚙️ Settings", "/")}
      <main>
        <section>
          <h2>Estado del server</h2>
          <div class="card compact">
            <div style="display:flex; justify-content:space-between; padding: 4px 0;">
              <span class="muted">API status</span>
              <span class="tag">${escapeHtml(health?.status || "?")}</span>
            </div>
            <div style="display:flex; justify-content:space-between; padding: 4px 0;">
              <span class="muted">Excel cargado</span>
              <span class="tag ${health?.xlsx_present ? "" : "warn"}">${health?.xlsx_present ? "sí" : "no"}</span>
            </div>
            <div style="display:flex; justify-content:space-between; padding: 4px 0;">
              <span class="muted">DB lista</span>
              <span class="tag ${health?.db_present ? "" : "warn"}">${health?.db_present ? "sí" : "no"}</span>
            </div>
            <div style="display:flex; justify-content:space-between; padding: 4px 0;">
              <span class="muted">Server time</span>
              <span class="tabular" style="font-size:12px;">${escapeHtml(health?.now || "")}</span>
            </div>
          </div>
        </section>

        <section>
          <h2>Acciones</h2>
          <button class="btn primary full" data-onclick="refreshAll" style="margin-bottom:8px">⟳ Refrescar DB desde Excel</button>
          <a class="btn ghost full" href="${API.base}/api/download/excel"
             style="margin-bottom:8px"
             onclick="event.preventDefault(); downloadExcel();">⬇ Descargar Excel</a>
          <button class="btn danger full" data-onclick="logout">🔓 Cerrar sesión</button>
        </section>

        <section>
          <h2>Vista por defecto</h2>
          <div class="card">
            <div class="toggle-pill" style="display: flex;">
              <button data-onclick="setView" data-arg="all" class="${API.view() === "all" ? "active" : ""}">📦 Todo</button>
              <button data-onclick="setView" data-arg="investible" class="${API.view() === "investible" ? "active" : ""}">💎 Solo invertible</button>
            </div>
            <hr/>
            <div style="font-size:13px; color: var(--muted);">Moneda ancla</div>
            <div class="toggle-pill" style="display: flex; margin-top:6px;">
              ${["USD", "USB", "ARS"].map(a =>
                `<button data-onclick="setAnchor" data-arg="${a}" class="${API.anchor() === a ? "active" : ""}">${a}</button>`
              ).join("")}
            </div>
          </div>
        </section>

        <section>
          <h2>Backups recientes (server)</h2>
          ${backups.length === 0 ? '<div class="card muted">Sin backups</div>' :
            `<div class="card" style="font-size:12px;">
              ${backups.slice(0, 5).map(b => `
                <div style="padding: 4px 0; border-bottom: 1px solid var(--border);">
                  <div>${escapeHtml(b.name)}</div>
                  <div class="muted">${escapeHtml(b.mtime)} · ${(b.size_bytes / 1024).toFixed(0)} KB</div>
                </div>
              `).join("")}
            </div>`
          }
        </section>

        <section>
          <h2>Sobre</h2>
          <div class="card compact muted" style="font-size:13px;">
            wm_engine v${escapeHtml(health?.version || "1.0")} · personal use only<br/>
            <a href="https://github.com/rodrigocorvalan93/wealth_management_rodricor"
               target="_blank">repo en GitHub</a>
          </div>
        </section>
      </main>
    `;
  });

  window.downloadExcel = async function () {
    try {
      toast("Descargando...", "info");
      const res = await fetch(`${API.base}/api/download/excel`, {
        headers: { Authorization: `Bearer ${API.token()}` }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "wealth_management_rodricor.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast(e.message, "error");
    }
  };

  // -------- Field templates --------
  function selectField(label, name, value, options, opts = {}) {
    const required = opts.required ? "required" : "";
    const allowEmpty = opts.allowEmpty !== false;
    return `
      <div class="field">
        <label>${escapeHtml(label)}${opts.required ? " *" : ""}</label>
        <select name="${escapeHtml(name)}" ${required}>
          ${allowEmpty ? '<option value="">—</option>' : ""}
          ${options.map(o => `<option value="${escapeHtml(o)}" ${o === value ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")}
        </select>
      </div>
    `;
  }
  function inputField(label, name, value, type = "text", opts = {}) {
    const required = opts.required ? "required" : "";
    const step = type === "number" ? `step="any"` : "";
    return `
      <div class="field">
        <label>${escapeHtml(label)}${opts.required ? " *" : ""}</label>
        <input type="${type}" name="${escapeHtml(name)}"
               value="${value !== null && value !== undefined ? escapeHtml(value) : ""}"
               ${step} ${required} placeholder="${escapeHtml(opts.placeholder || "")}">
      </div>
    `;
  }

  function tradeFormFields(row, meta) {
    return `
      <div class="field-row">
        ${inputField("Trade ID", "Trade ID", row["Trade ID"], "text", { placeholder: "ej T0042" })}
        ${inputField("Trade Date", "Trade Date", fmt.date(row["Trade Date"]) || fmt.today(), "date", { required: true })}
      </div>
      ${selectField("Side", "Side", row.Side, ["BUY", "SELL"], { required: true })}
      <div class="field-row">
        ${selectField("Cuenta", "Cuenta", row.Cuenta, meta.accounts, { required: true })}
        ${selectField("Cuenta Cash", "Cuenta Cash", row["Cuenta Cash"] || row.Cuenta, meta.accounts)}
      </div>
      ${selectField("Ticker", "Ticker", row.Ticker, meta.tickers, { required: true })}
      <div class="field-row">
        ${inputField("Qty", "Qty", row.Qty, "number", { required: true })}
        ${inputField("Precio", "Precio", row.Precio, "number", { required: true })}
      </div>
      <div class="field-row">
        ${selectField("Moneda Trade", "Moneda Trade", row["Moneda Trade"], meta.currencies, { required: true })}
        ${inputField("Settle Date", "Settle Date", fmt.date(row["Settle Date"]), "date")}
      </div>
      ${inputField("Comisión", "Comisión", row["Comisión"], "number")}
      ${inputField("Strategy", "Strategy", row.Strategy, "text", { placeholder: "TRADING / BH / etc" })}
      ${inputField("Description", "Description", row.Description)}
      ${inputField("Notes", "Notes", row.Notes)}
    `;
  }
  function gastoFormFields(row, meta) {
    return `
      ${inputField("Fecha", "Fecha", fmt.date(row.Fecha) || fmt.today(), "date", { required: true })}
      ${inputField("Concepto", "Concepto", row.Concepto, "text", { required: true })}
      <div class="field-row">
        ${inputField("Monto", "Monto", row.Monto, "number", { required: true })}
        ${selectField("Moneda", "Moneda", row.Moneda, meta.currencies, { required: true })}
      </div>
      ${selectField("Cuenta Destino", "Cuenta Destino", row["Cuenta Destino"], meta.accounts, { required: true })}
      <div class="field-row">
        ${inputField("Categoría", "Categoría", row.Categoría || row.Categoria, "text",
                     { placeholder: "Vivienda, Comida, ..." })}
        ${selectField("Tipo", "Tipo", row.Tipo, ["FIJO", "VARIABLE"], { allowEmpty: true })}
      </div>
      ${inputField("Cuotas", "Cuotas", row.Cuotas || 1, "number")}
      ${inputField("Notes", "Notes", row.Notes)}
    `;
  }
  function ingresoFormFields(row, meta) {
    return `
      ${inputField("Fecha", "Fecha", fmt.date(row.Fecha) || fmt.today(), "date", { required: true })}
      ${inputField("Concepto", "Concepto", row.Concepto, "text", { required: true })}
      ${inputField("Categoría", "Categoría", row.Categoría || row.Categoria, "text",
                   { placeholder: "Sueldo, Cupón, Dividendo, ..." })}
      <div class="field-row">
        ${inputField("Monto", "Monto", row.Monto, "number", { required: true })}
        ${selectField("Moneda", "Moneda", row.Moneda, meta.currencies, { required: true })}
      </div>
      ${selectField("Cuenta Destino", "Cuenta Destino", row["Cuenta Destino"], meta.accounts, { required: true })}
      ${inputField("Description", "Description", row.Description)}
      ${inputField("Notes", "Notes", row.Notes)}
    `;
  }
  function transferFormFields(row, meta) {
    return `
      ${inputField("Fecha", "Fecha", fmt.date(row.Fecha) || fmt.today(), "date", { required: true })}
      <div class="field-row">
        ${selectField("Cuenta Origen", "Cuenta Origen", row["Cuenta Origen"], meta.accounts, { required: true })}
        ${selectField("Cuenta Destino", "Cuenta Destino", row["Cuenta Destino"], meta.accounts, { required: true })}
      </div>
      <div class="field-row">
        ${inputField("Monto", "Monto", row.Monto, "number", { required: true })}
        ${selectField("Moneda", "Moneda", row.Moneda, meta.currencies, { required: true })}
      </div>
      ${inputField("Description", "Description", row.Description)}
    `;
  }

  // -------- Helpers de UI --------
  function headerWithBack(title, backHref) {
    return `
      <div class="topbar">
        <button onclick="window.history.length>1 ? window.history.back() : window.location.hash='${backHref}'">‹ Atrás</button>
        <h1>${escapeHtml(title)}</h1>
        <div></div>
      </div>
    `;
  }
  function emptyState(title, msg) {
    return `<div class="empty"><div class="icon">📭</div><div><b>${escapeHtml(title)}</b></div><div class="muted" style="margin-top:4px">${escapeHtml(msg)}</div></div>`;
  }

  // -------- Bootstrap --------

  // Si no hay token → login screen
  function showLogin() {
    document.body.innerHTML = `
      <div class="login-screen">
        <div class="logo">WM</div>
        <div class="tagline">Wealth Management</div>
        <form id="login-form">
          <div class="field">
            <label>API Token</label>
            <input type="password" name="token" placeholder="github_pat_... o tu token de 64 chars"
                   autocomplete="off" required>
          </div>
          <button type="submit" class="btn primary full" style="margin-top:12px">Entrar</button>
          <div style="text-align:center; margin-top:12px; font-size:12px; opacity:0.8;">
            El token se guarda solo en este dispositivo (localStorage).
          </div>
        </form>
      </div>
    `;
    document.getElementById("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const tok = e.target.token.value.trim();
      if (!tok) return;
      API.setToken(tok);
      // Verificar haciendo una llamada autenticada
      try {
        const data = await API.req("/api/config");
        if (data.anchor) {
          API.setAnchor(data.anchor);
        }
        window.location.reload();
      } catch (err) {
        toast("Token inválido o server caído: " + err.message, "error");
        API.setToken("");
      }
    });
  }

  function bootstrap() {
    if (!API.token()) {
      showLogin();
      return;
    }
    document.body.innerHTML = `
      <div id="root" class="app-shell"></div>
      <a id="fab" class="fab" href="#/trades/new" style="display:none">+</a>
      <nav class="bottom-nav">
        <a href="#/"><span class="icon">📊</span><span>Home</span></a>
        <a href="#/trades"><span class="icon">📈</span><span>Trades</span></a>
        <a href="#/gastos"><span class="icon">💸</span><span>Gastos</span></a>
        <a href="#/ingresos"><span class="icon">💰</span><span>Ingresos</span></a>
        <a href="#/performance"><span class="icon">🎯</span><span>Stats</span></a>
        <a href="#/settings"><span class="icon">⚙️</span><span>Más</span></a>
      </nav>
    `;
    window.addEventListener("hashchange", render);
    render();
  }

  // Service Worker registration
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/static/sw.js").catch(() => {});
    });
  }

  bootstrap();
})();
