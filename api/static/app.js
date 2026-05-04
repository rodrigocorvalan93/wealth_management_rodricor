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
    getSheetRow: (sheet, id) => API.req(`/api/sheets/${sheet}/${encodeURIComponent(id)}`),
    createRow: (sheet, data) => API.req(`/api/sheets/${sheet}`, { method: "POST", json: data }),
    updateRow: (sheet, id, data) => API.req(`/api/sheets/${sheet}/${encodeURIComponent(id)}`, { method: "PUT", json: data }),
    deleteRow: (sheet, id) => API.req(`/api/sheets/${sheet}/${encodeURIComponent(id)}`, { method: "DELETE" }),

    prices: () => API.req("/api/prices"),
    fxRates: () => API.req("/api/fx-rates"),
    cash: (anchor) => API.req(`/api/cash?anchor=${anchor || API.anchor()}`),
    config: () => API.req("/api/config"),
    reportHtml: (anchor) => API.req(`/api/report/html?anchor=${anchor || API.anchor()}`),
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
    // Maestros: especies (tickers)
    async createEspecie(data) {
      await API.createRow("especies", data);
      invalidateMeta();
      toast(`Especie ${data.Ticker} agregada ✓`, "success");
      navigate("/especies");
    },
    async updateEspecie(data, form) {
      const id = form.dataset.rowId;
      await API.updateRow("especies", id, data);
      invalidateMeta();
      toast("Especie actualizada ✓", "success");
      navigate("/especies");
    },
    async deleteEspecie(id) {
      if (!confirm(`¿Borrar especie ${id}? Movimientos previos quedarán huérfanos.`)) return;
      await API.deleteRow("especies", id);
      invalidateMeta();
      toast("Especie borrada", "success");
      render();
    },
    // Maestros: monedas
    async createMoneda(data) {
      ["Is Stable", "Is Base"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined) {
          data[k] = data[k] === "1" || data[k] === 1 || data[k] === true ? 1 : 0;
        }
      });
      await API.createRow("monedas", data);
      invalidateMeta();
      toast(`Moneda ${data.Code} agregada ✓`, "success");
      navigate("/monedas");
    },
    async updateMoneda(data, form) {
      ["Is Stable", "Is Base"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined) {
          data[k] = data[k] === "1" || data[k] === 1 || data[k] === true ? 1 : 0;
        }
      });
      const id = form.dataset.rowId;
      await API.updateRow("monedas", id, data);
      invalidateMeta();
      toast("Moneda actualizada ✓", "success");
      navigate("/monedas");
    },
    async deleteMoneda(id) {
      if (!confirm(`¿Borrar moneda ${id}? Solo si NO se usa en ningún movement.`)) return;
      await API.deleteRow("monedas", id);
      invalidateMeta();
      toast("Moneda borrada", "success");
      render();
    },
    // Maestros: cuentas
    async createCuenta(data) {
      ["Close Day", "Due Day"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined && data[k] !== "") {
          data[k] = parseInt(data[k]);
        }
      });
      await API.createRow("cuentas", data);
      invalidateMeta();
      toast(`Cuenta ${data.Code} agregada ✓`, "success");
      navigate("/cuentas");
    },
    async updateCuenta(data, form) {
      ["Close Day", "Due Day"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined && data[k] !== "") {
          data[k] = parseInt(data[k]);
        }
      });
      const id = form.dataset.rowId;
      await API.updateRow("cuentas", id, data);
      invalidateMeta();
      toast("Cuenta actualizada ✓", "success");
      navigate("/cuentas");
    },
    async deleteCuenta(id) {
      if (!confirm(`¿Borrar cuenta ${id}? Solo si NO tiene movements.`)) return;
      await API.deleteRow("cuentas", id);
      invalidateMeta();
      toast("Cuenta borrada", "success");
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
          <a href="#/report" class="btn primary full">📄 Ver reporte completo</a>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px;">
            <a href="#/cash" class="btn ghost full">💵 Cash por cuenta</a>
            <a href="#/cotizaciones" class="btn ghost full">💹 Cotizaciones</a>
          </div>
        </section>
      </main>
    `;
  });

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

  // ====================================================================
  // Maestros: especies
  // ====================================================================
  route("/especies", async () => {
    const data = await API.listSheet("especies");
    const items = (data.items || []).filter(r => r.Ticker);
    items.sort((a, b) => (a["Asset Class"] || "").localeCompare(b["Asset Class"] || "")
                       || (a.Ticker || "").localeCompare(b.Ticker || ""));
    return `
      ${headerWithBack("📦 Especies", "/settings")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Tickers tradeables. Cualquier especie que uses en blotter debe estar acá.
          También editable desde el Excel master.
        </div>
        <a href="#/especies/new" class="btn primary full" style="margin: 12px 0;">+ Nueva especie</a>
        ${items.length === 0 ? emptyState("Sin especies", "Tocá + para agregar") :
          `<div class="list">${items.map(r => `
            <a class="list-item" href="#/especies/${encodeURIComponent(r.Ticker)}/edit">
              <div class="meta">
                <div class="meta-line1">${escapeHtml(r.Ticker)} · <span class="muted">${escapeHtml(r["Asset Class"] || "?")}</span></div>
                <div class="meta-line2">${escapeHtml(r.Name || "")}</div>
              </div>
              <div class="right">
                <div class="sub"><span class="tag">${escapeHtml(r.Currency || "")}</span></div>
                ${r.Issuer ? `<div class="sub muted">${escapeHtml(r.Issuer)}</div>` : ""}
              </div>
            </a>
          `).join("")}</div>`
        }
      </main>
    `;
  });

  route("/especies/new", async () => {
    return `
      ${headerWithBack("Nueva especie", "/especies")}
      <main>
        <form data-action="createEspecie">
          ${especieFormFields({}, false)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  route("/especies/:id/edit", async ({ id }) => {
    const row = await API.getSheetRow("especies", id);
    return `
      ${headerWithBack("Editar especie", "/especies")}
      <main>
        <form data-action="updateEspecie" data-row-id="${escapeHtml(id)}">
          ${especieFormFields(row, true)}
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteEspecie" data-arg="${escapeHtml(id)}">🗑 Borrar</button>
      </main>
    `;
  });

  // ====================================================================
  // Maestros: monedas
  // ====================================================================
  route("/monedas", async () => {
    const data = await API.listSheet("monedas");
    const items = (data.items || []).filter(r => r.Code);
    return `
      ${headerWithBack("💱 Monedas", "/settings")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Monedas y stablecoins del sistema. También editable desde Excel.
        </div>
        <a href="#/monedas/new" class="btn primary full" style="margin: 12px 0;">+ Nueva moneda</a>
        <div class="list">${items.map(r => `
          <a class="list-item" href="#/monedas/${encodeURIComponent(r.Code)}/edit">
            <div class="meta">
              <div class="meta-line1">${escapeHtml(r.Code)} ${r["Is Base"] ? '<span class="tag">base</span>' : ""} ${r["Is Stable"] ? '<span class="tag">stable</span>' : ""}</div>
              <div class="meta-line2">${escapeHtml(r.Name || "")} ${r["Quote vs"] ? `· quote vs ${escapeHtml(r["Quote vs"])}` : ""}</div>
            </div>
          </a>
        `).join("")}</div>
      </main>
    `;
  });

  route("/monedas/new", async () => {
    return `
      ${headerWithBack("Nueva moneda", "/monedas")}
      <main>
        <form data-action="createMoneda">
          ${monedaFormFields({}, false)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  route("/monedas/:id/edit", async ({ id }) => {
    const row = await API.getSheetRow("monedas", id);
    return `
      ${headerWithBack("Editar moneda", "/monedas")}
      <main>
        <form data-action="updateMoneda" data-row-id="${escapeHtml(id)}">
          ${monedaFormFields(row, true)}
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteMoneda" data-arg="${escapeHtml(id)}">🗑 Borrar</button>
      </main>
    `;
  });

  // ====================================================================
  // Maestros: cuentas
  // ====================================================================
  route("/cuentas", async () => {
    const data = await API.listSheet("cuentas");
    const items = (data.items || []).filter(r => r.Code);
    return `
      ${headerWithBack("🏦 Cuentas", "/settings")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Brokers, bancos, wallets, tarjetas. Marcá <b>Investible=NO</b>
          para excluir del PN invertible (ej cash de reserva).
        </div>
        <a href="#/cuentas/new" class="btn primary full" style="margin: 12px 0;">+ Nueva cuenta</a>
        <div class="list">${items.map(r => `
          <a class="list-item" href="#/cuentas/${encodeURIComponent(r.Code)}/edit">
            <div class="meta">
              <div class="meta-line1">${escapeHtml(r.Code)}
                ${r.Investible === "NO" ? '<span class="tag warn">no-inv</span>' : ""}
              </div>
              <div class="meta-line2">${escapeHtml(r.Name || "")} · <span class="muted">${escapeHtml(r.Kind || "")}</span></div>
            </div>
            <div class="right">
              ${r.Currency ? `<div class="sub"><span class="tag">${escapeHtml(r.Currency)}</span></div>` : ""}
            </div>
          </a>
        `).join("")}</div>
      </main>
    `;
  });

  route("/cuentas/new", async () => {
    const meta = await loadMeta();
    return `
      ${headerWithBack("Nueva cuenta", "/cuentas")}
      <main>
        <form data-action="createCuenta">
          ${cuentaFormFields({}, meta, false)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  route("/cuentas/:id/edit", async ({ id }) => {
    const meta = await loadMeta();
    const row = await API.getSheetRow("cuentas", id);
    return `
      ${headerWithBack("Editar cuenta", "/cuentas")}
      <main>
        <form data-action="updateCuenta" data-row-id="${escapeHtml(id)}">
          ${cuentaFormFields(row, meta, true)}
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteCuenta" data-arg="${escapeHtml(id)}">🗑 Borrar</button>
      </main>
    `;
  });

  // ====================================================================
  // Cotizaciones (precios + FX)
  // ====================================================================
  route("/cotizaciones", async () => {
    const [pr, fx] = await Promise.all([API.prices(), API.fxRates()]);
    const prices = pr.items || [];
    const fxRates = fx.items || [];
    // Agrupar precios por asset_class
    const byClass = {};
    prices.forEach(p => {
      const cls = p.asset_class || "?";
      if (!byClass[cls]) byClass[cls] = [];
      byClass[cls].push(p);
    });
    return `
      ${headerWithBack("💹 Cotizaciones", "/")}
      <main>
        <section>
          <h2>FX rates (${fxRates.length})</h2>
          <div class="card">
            ${fxRates.length === 0 ? '<div class="muted">Sin FX cargado. Subí fx_historico.csv vía sync.py.</div>' :
              `<table style="width:100%; font-size: 14px;">
                <thead>
                  <tr style="border-bottom: 1px solid var(--border);">
                    <th style="text-align:left; padding: 4px;">Moneda</th>
                    <th style="text-align:right; padding: 4px;">Rate</th>
                    <th style="text-align:left; padding: 4px;">Base</th>
                    <th style="text-align:left; padding: 4px; font-size:11px;">Fecha</th>
                  </tr>
                </thead>
                <tbody>${fxRates.map(r => `
                  <tr style="border-bottom: 1px solid var(--border);">
                    <td style="padding: 4px;"><b>${escapeHtml(r.moneda)}</b></td>
                    <td style="padding: 4px; text-align:right;" class="tabular">${fmt.money(r.rate, 4)}</td>
                    <td style="padding: 4px;">${escapeHtml(r.base)}</td>
                    <td style="padding: 4px; font-size:11px;" class="muted">${escapeHtml(r.fecha)}</td>
                  </tr>
                `).join("")}</tbody>
              </table>`
            }
          </div>
        </section>

        <section>
          <h2>Precios por activo (${prices.length})</h2>
          ${prices.length === 0 ? '<div class="card muted">Sin precios cargados. Corré loaders y subí los CSVs (ver Settings → Ayuda).</div>' :
            Object.keys(byClass).sort().map(cls => `
              <div class="card" style="margin-bottom: 8px;">
                <div style="font-weight: 600; color: var(--navy); margin-bottom: 6px;">${escapeHtml(cls)} (${byClass[cls].length})</div>
                <table style="width:100%; font-size: 13px;">
                  <tbody>${byClass[cls].map(p => `
                    <tr style="border-bottom: 1px solid var(--border);">
                      <td style="padding: 4px;"><b>${escapeHtml(p.ticker)}</b><br><span class="muted" style="font-size:11px;">${escapeHtml(p.name || "")}</span></td>
                      <td style="padding: 4px; text-align:right;" class="tabular">${fmt.money(p.price, 4)} <span class="muted" style="font-size:11px;">${escapeHtml(p.currency)}</span></td>
                      <td style="padding: 4px; text-align:right; font-size:11px;" class="muted">${escapeHtml(p.fecha)}<br>${escapeHtml(p.source)}</td>
                    </tr>
                  `).join("")}</tbody>
                </table>
              </div>
            `).join("")
          }
        </section>
      </main>
    `;
  });

  // ====================================================================
  // Cash por cuenta
  // ====================================================================
  route("/cash", async () => {
    const data = await API.cash();
    const items = data.items || [];
    const byCcy = data.by_currency || {};
    return `
      ${headerWithBack("💵 Cash por cuenta", "/")}
      <main>
        <div class="kpi primary">
          <div class="kpi-label">Cash total</div>
          <div class="kpi-value">${fmt.money(data.total_anchor)}</div>
          <div class="kpi-currency">${escapeHtml(data.anchor)}</div>
        </div>

        <section>
          <h2>Por moneda</h2>
          <div class="card">
            ${Object.keys(byCcy).length === 0 ? '<div class="muted">Sin cash</div>' :
              Object.entries(byCcy).sort((a, b) => b[1].mv_anchor - a[1].mv_anchor).map(([ccy, sub]) => `
                <div style="display:flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border);">
                  <div>
                    <b>${escapeHtml(ccy)}</b>
                    <span class="muted" style="font-size: 12px;">(${sub.n} cuentas)</span>
                  </div>
                  <div class="right">
                    <div class="tabular">${fmt.money(sub.qty, 2)} ${escapeHtml(ccy)}</div>
                    <div class="muted tabular" style="font-size: 12px;">${fmt.money(sub.mv_anchor)} ${escapeHtml(data.anchor)}</div>
                  </div>
                </div>
              `).join("")
            }
          </div>
        </section>

        <section>
          <h2>Por cuenta (${items.length})</h2>
          ${items.length === 0 ? '<div class="card muted">Sin cash en ninguna cuenta</div>' :
            `<div class="list">${items.map(h => `
              <div class="list-item">
                <div class="meta">
                  <div class="meta-line1">
                    ${escapeHtml(h.account)}
                    ${!h.investible ? '<span class="tag warn">no-inv</span>' : ""}
                  </div>
                  <div class="meta-line2">
                    ${escapeHtml(h.account_kind)} · ${escapeHtml(h.cash_purpose || "—")}
                  </div>
                </div>
                <div class="right">
                  <div class="amount tabular">${fmt.money(h.qty, 2)} ${escapeHtml(h.currency)}</div>
                  <div class="sub muted">${fmt.money(h.mv_anchor)} ${escapeHtml(data.anchor)}</div>
                </div>
              </div>
            `).join("")}</div>`
          }
        </section>
      </main>
    `;
  });

  // ====================================================================
  // /report — reporte HTML inline
  // ====================================================================
  route("/report", async () => {
    const html = await API.reportHtml();
    // Escape para srcdoc
    const escaped = html.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
    return `
      <div class="topbar">
        <button onclick="window.history.back()">‹ Atrás</button>
        <h1>📄 Reporte</h1>
        <button onclick="openReportInBrowser()" title="Abrir externo">↗</button>
      </div>
      <iframe srcdoc="${escaped}"
              style="width:100%; height: calc(100vh - 60px - var(--safe-top)); border: none; display: block;"
              sandbox="allow-scripts allow-same-origin allow-popups"></iframe>
    `;
  });

  // Helper: abrir el reporte en el browser externo (Safari/Chrome) saliendo del PWA standalone
  window.openReportInBrowser = async function () {
    try {
      const html = await API.reportHtml();
      const blob = new Blob([html], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      // En iOS standalone, esto abre en Safari (sale de la PWA pero permite imprimir/compartir)
      window.open(url, "_blank") || (window.location.href = url);
    } catch (e) {
      toast(e.message, "error");
    }
  };

  // ====================================================================
  // /help — manualcitos
  // ====================================================================
  route("/help", async () => {
    return `
      ${headerWithBack("❓ Ayuda", "/settings")}
      <main>
        <section>
          <h2>📥 ¿Dónde se descarga el "Reporte completo" en iOS PWA?</h2>
          <div class="card">
            <p style="margin-bottom: 8px;">Cuando la app está instalada en el iPhone, los archivos
            descargados van a la app <b>"Archivos"</b> (Files), carpeta <b>"En mi iPhone" → "WM"</b>
            o "Descargas".</p>
            <p style="margin-bottom: 8px;">Para evitar el problema, ahora <b>Ver reporte completo</b>
            abre el reporte <b>inline</b> dentro de la app (no descarga). Si querés
            compartirlo o imprimirlo, tocá la flecha ↗ arriba a la derecha — abre
            en Safari y desde ahí podés "Compartir" o "Guardar PDF".</p>
          </div>
        </section>

        <section>
          <h2>🔄 Sincronización entre PC ↔ Cloud</h2>
          <div class="card">
            <p><b>Source of truth</b>: el Excel master vive en el server.</p>
            <p style="margin-top: 8px;"><b>Si cargás algo desde la PWA</b>: se actualiza automáticamente en el server.
            Tu Excel local queda <i>desactualizado</i> hasta que lo bajes.</p>
            <p style="margin-top: 8px;"><b>Si editás el Excel local</b>: tenés que subirlo al server.</p>

            <h3 style="margin-top: 14px;">Comandos en tu PC (PowerShell)</h3>
            <p style="margin-top: 4px;">Asegurate de tener este token en <code>secrets.txt</code>:</p>
            <pre style="background: #f5f5f5; padding: 8px; border-radius: 4px; font-size: 11px; overflow-x: auto;">WM_API_TOKEN=tu_token_aqui</pre>

            <p style="margin-top: 12px;">Después corré:</p>
            <pre style="background: #f5f5f5; padding: 8px; border-radius: 4px; font-size: 12px; overflow-x: auto;"># Ciclo completo (loaders + uploads + refresh)
python sync.py

# Solo subir el Excel local (si lo editaste)
python sync.py --only-excel

# Solo subir CSVs (si solo refrescaste precios)
python sync.py --only-prices

# Sin correr loaders (usar CSVs ya generados)
python sync.py --skip-loaders

# Bajar el reporte HTML al final
python sync.py --download</pre>
          </div>
        </section>

        <section>
          <h2>📈 Cómo correr loaders y subir cotizaciones</h2>
          <div class="card">
            <p>Los loaders corren <b>en tu PC</b> (no en PA por la whitelist).
            Generan CSVs en <code>data/</code>, después se suben al server.</p>

            <h3 style="margin-top: 14px;">Loaders disponibles</h3>
            <pre style="background: #f5f5f5; padding: 8px; border-radius: 4px; font-size: 11px; overflow-x: auto;"># FX rates (MEP, CCL, mayorista)
python fx_loader.py

# BYMA: bonos, acciones, CEDEAR
python byma_loader.py --tickers-file mis_tickers.txt

# CAFCI: FCIs argentinos
python cafci_loader.py

# Cripto (CoinGecko)
python cripto_loader.py

# US (yfinance — ADRs)
python yfinance_loader.py</pre>

            <h3 style="margin-top: 14px;">Subir CSVs al server</h3>
            <p>El comando más fácil:</p>
            <pre style="background: #f5f5f5; padding: 8px; border-radius: 4px; font-size: 12px; overflow-x: auto;">python sync.py --skip-loaders --skip-excel</pre>
            <p style="margin-top: 8px;">O todo de una sin pensar:</p>
            <pre style="background: #f5f5f5; padding: 8px; border-radius: 4px; font-size: 12px; overflow-x: auto;">python sync.py</pre>

            <p style="margin-top: 8px;" class="muted">Después de subir, el server re-importa la DB y los precios
            quedan actualizados al instante en el reporte.</p>
          </div>
        </section>

        <section>
          <h2>🔄 ¿Cuándo refrescar la DB manualmente?</h2>
          <div class="card">
            <p>Generalmente <b>no hace falta</b> — cada vez que la PWA hace
            POST/PUT/DELETE, el server re-importa solo. Pero si subiste algo
            por SFTP o tocaste el Excel directamente en el server, andá a
            Settings y tocá <b>"⟳ Refrescar DB"</b>.</p>
          </div>
        </section>

        <section>
          <h2>📦 Editar maestros (especies / monedas / cuentas)</h2>
          <div class="card">
            <p>Desde Settings tenés acceso a <b>Especies</b>, <b>Monedas</b> y
            <b>Cuentas</b>. Cualquier cambio se appendea al Excel master del server
            y se re-importa al toque. <b>Excel local</b> sigue funcionando: si
            preferís cargar grandes batches, hacelo en Excel y subilo con
            <code>python sync.py --only-excel</code>.</p>
            <p style="margin-top: 8px;"><b>Borrar especies/monedas/cuentas</b>: hard-delete
            (remueve la fila físicamente). Solo borrá si NO tienen movements asociados,
            sino vas a romper cargas viejas.</p>
          </div>
        </section>

        <section>
          <h2>🔐 Privacidad de tu token</h2>
          <div class="card">
            <p>Tu API token vive solo en el localStorage de este device. No lo
            mandés por chat ni lo subas al repo. Si lo perdés, regeneralo desde
            tu PC con:</p>
            <pre style="background: #f5f5f5; padding: 8px; border-radius: 4px; font-size: 12px; overflow-x: auto;">python -c "import secrets; print(secrets.token_hex(32))"</pre>
            <p style="margin-top: 8px;">Y actualizá el WSGI file en PythonAnywhere.</p>
          </div>
        </section>

        <section>
          <h2>📅 ¿Por qué se "desactiva" cada 30 días?</h2>
          <div class="card">
            <p>PythonAnywhere free tier desactiva el sitio si no entrás
            al menos 1 vez por mes. Te llega email 7 días antes. Para
            extender: PA → Web → click "Run until 1 month from today".</p>
            <p style="margin-top: 8px;" class="muted">Los datos NO se borran si se
            desactiva — solo se apaga. Re-activás haciendo login + reload.</p>
          </div>
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
          <h2>Maestros</h2>
          <a href="#/especies" class="btn ghost full" style="margin-bottom:6px">📦 Especies</a>
          <a href="#/monedas" class="btn ghost full" style="margin-bottom:6px">💱 Monedas</a>
          <a href="#/cuentas" class="btn ghost full" style="margin-bottom:6px">🏦 Cuentas</a>
        </section>

        <section>
          <h2>Cotizaciones y cash</h2>
          <a href="#/cotizaciones" class="btn ghost full" style="margin-bottom:6px">💹 Cotizaciones (precios + FX)</a>
          <a href="#/cash" class="btn ghost full" style="margin-bottom:6px">💵 Cash por cuenta</a>
          <a href="#/pasivos" class="btn ghost full" style="margin-bottom:6px">📉 Pasivos</a>
        </section>

        <section>
          <h2>Acciones</h2>
          <button class="btn primary full" data-onclick="refreshAll" style="margin-bottom:8px">⟳ Refrescar DB desde Excel</button>
          <a class="btn ghost full" href="${API.base}/api/download/excel"
             style="margin-bottom:8px"
             onclick="event.preventDefault(); downloadExcel();">⬇ Descargar Excel</a>
          <a class="btn ghost full" href="#/help" style="margin-bottom:8px">❓ Ayuda y manualcitos</a>
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
    const readonly = opts.readonly ? "readonly style='background:#F0F0F0;'" : "";
    return `
      <div class="field">
        <label>${escapeHtml(label)}${opts.required ? " *" : ""}${opts.readonly ? " (no editable)" : ""}</label>
        <input type="${type}" name="${escapeHtml(name)}"
               value="${value !== null && value !== undefined ? escapeHtml(value) : ""}"
               ${step} ${required} ${readonly}
               placeholder="${escapeHtml(opts.placeholder || "")}">
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

  // -------- Form helpers para maestros --------
  const ASSET_CLASSES = ["CASH", "BOND_AR", "EQUITY_AR", "EQUITY_US", "FCI",
                          "CRYPTO", "STABLECOIN", "DERIVATIVE", "OTHER"];
  const ACCOUNT_KINDS = ["CASH_BANK", "CASH_BROKER", "CASH_WALLET", "CASH_PHYSICAL",
                          "CARD_CREDIT", "LIABILITY", "EXTERNAL", "OPENING_BALANCE",
                          "INTEREST_EXPENSE", "INTEREST_INCOME"];

  function especieFormFields(row, isEdit) {
    return `
      ${inputField("Ticker (key)", "Ticker", row.Ticker, "text",
                    { required: true, placeholder: "ej AL30D", readonly: isEdit })}
      ${inputField("Name", "Name", row.Name, "text", { required: true })}
      ${selectField("Asset Class", "Asset Class", row["Asset Class"], ASSET_CLASSES, { required: true })}
      ${inputField("Currency", "Currency", row.Currency, "text",
                    { required: true, placeholder: "USB / USD / ARS / ..." })}
      ${inputField("Issuer", "Issuer", row.Issuer)}
      ${inputField("Sector", "Sector", row.Sector)}
      ${inputField("Country", "Country", row.Country)}
      ${inputField("Notes", "Notes", row.Notes)}
    `;
  }

  function monedaFormFields(row, isEdit) {
    const isStable = row["Is Stable"] === 1 || row["Is Stable"] === "1" || row["Is Stable"] === true;
    const isBase = row["Is Base"] === 1 || row["Is Base"] === "1" || row["Is Base"] === true;
    return `
      ${inputField("Code (key)", "Code", row.Code, "text",
                    { required: true, placeholder: "ej USDC", readonly: isEdit })}
      ${inputField("Name", "Name", row.Name, "text", { required: true })}
      ${inputField("Quote vs", "Quote vs", row["Quote vs"], "text",
                    { placeholder: "ARS / USD / null si es base" })}
      <div class="field">
        <label>Is Stable (1=stablecoin)</label>
        <select name="Is Stable">
          <option value="0" ${!isStable ? "selected" : ""}>0 (no)</option>
          <option value="1" ${isStable ? "selected" : ""}>1 (sí)</option>
        </select>
      </div>
      <div class="field">
        <label>Is Base (1=moneda base de reporte)</label>
        <select name="Is Base">
          <option value="0" ${!isBase ? "selected" : ""}>0 (no)</option>
          <option value="1" ${isBase ? "selected" : ""}>1 (sí)</option>
        </select>
      </div>
      ${inputField("Notas", "Notas", row.Notas)}
    `;
  }

  function cuentaFormFields(row, meta, isEdit) {
    const isInv = (row.Investible === "YES" || row.Investible === 1 || row.Investible === undefined);
    return `
      ${inputField("Code (key)", "Code", row.Code, "text",
                    { required: true, placeholder: "ej cocos / galicia", readonly: isEdit })}
      ${inputField("Name", "Name", row.Name, "text", { required: true })}
      ${selectField("Kind", "Kind", row.Kind, ACCOUNT_KINDS, { required: true })}
      ${inputField("Institution", "Institution", row.Institution)}
      ${inputField("Currency", "Currency", row.Currency || "ARS", "text",
                    { placeholder: "ARS / USD / USB / ..." })}
      <div class="field">
        <label>Investible</label>
        <select name="Investible">
          <option value="YES" ${isInv ? "selected" : ""}>YES (cuenta el PN invertible)</option>
          <option value="NO" ${!isInv ? "selected" : ""}>NO (excluir del PN invertible)</option>
        </select>
      </div>
      ${inputField("Cash Purpose", "Cash Purpose", row["Cash Purpose"], "text",
                    { placeholder: "OPERATIVO / RESERVA_NO_DECLARADO / ..." })}
      <hr style="margin: 8px 0; border: none; border-top: 1px solid var(--border);">
      <div style="font-size: 11px; color: var(--muted); margin-bottom: 4px;">
        Solo si es CARD_CREDIT (tarjeta):
      </div>
      ${selectField("Card Cycle", "Card Cycle", row["Card Cycle"] || "NONE", ["NONE", "MONTHLY"])}
      ${inputField("Close Day", "Close Day", row["Close Day"], "number", { placeholder: "1-31" })}
      ${inputField("Due Day", "Due Day", row["Due Day"], "number", { placeholder: "1-31" })}
      ${inputField("Card Currency", "Card Currency", row["Card Currency"], "text", { placeholder: "ARS / USD" })}
      ${inputField("Notes", "Notes", row.Notes)}
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
