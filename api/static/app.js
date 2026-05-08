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

    // Cache simple en memoria con TTL.
    // Solo cachea GETs JSON. POST/PUT/DELETE invalidan caches relacionadas.
    _cache: new Map(),       // path → { data, ts }
    _cacheTTL: 30 * 1000,    // 30s default

    _bustCache(pattern) {
      // Invalida cache para paths que matchean (substring)
      for (const k of Array.from(this._cache.keys())) {
        if (!pattern || k.includes(pattern)) this._cache.delete(k);
      }
    },

    async req(path, opts = {}) {
      const url = `${this.base}${path}`;
      const method = (opts.method || "GET").toUpperCase();
      const isCacheable = method === "GET" && !opts.noCache;
      const cacheKey = isCacheable ? path : null;

      // Cache hit
      if (cacheKey) {
        const hit = this._cache.get(cacheKey);
        if (hit && Date.now() - hit.ts < this._cacheTTL) {
          return hit.data;
        }
      }

      const headers = { ...(opts.headers || {}) };
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
        // Si tenemos data stale en cache, devolvemos esa con warning
        if (cacheKey) {
          const stale = this._cache.get(cacheKey);
          if (stale) {
            console.warn("Sin conexión, sirviendo stale cache:", cacheKey);
            return stale.data;
          }
        }
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
      const data = ct.includes("application/json") ? await res.json() : await res.text();

      // Guardar en cache si era GET
      if (cacheKey) {
        this._cache.set(cacheKey, { data, ts: Date.now() });
      }
      // Mutaciones invalidan caches relacionadas
      if (method !== "GET") {
        // Invalidar todo lo que tenga que ver con sheets/holdings/summary/etc
        this._bustCache();
      }
      return data;
    },

    // Endpoints específicos
    health: () => API.req("/api/health", { skipAuth: true }),
    summary: (anchor) => API.req(`/api/summary?anchor=${anchor || API.anchor()}`),
    holdings: (anchor) => API.req(`/api/holdings?anchor=${anchor || API.anchor()}`),
    tradeStats: (anchor) => API.req(`/api/trade-stats?anchor=${anchor || API.anchor()}`),
    buyingPower: (anchor) => API.req(`/api/buying-power?anchor=${anchor || API.anchor()}`),
    realizedPnl: () => API.req(`/api/realized-pnl`),
    assetHistory: (ticker, account) => {
      const qs = account ? `?account=${encodeURIComponent(account)}` : "";
      return API.req(`/api/asset/${encodeURIComponent(ticker)}/history${qs}`);
    },
    assetPerformance: (anchor, investible) => API.req(
      `/api/asset-performance?anchor=${anchor || API.anchor()}&investible=${!!investible}`
    ),
    cashPerformance: (anchor) => API.req(
      `/api/cash-performance?anchor=${anchor || API.anchor()}`
    ),
    equityCurve: (anchor, investible) => API.req(
      `/api/equity-curve?anchor=${anchor || API.anchor()}&investible=${!!investible}`
    ),
    deleteSnapshots: (opts) => {
      const qs = opts && opts.all ? "all=1"
        : opts && opts.before ? `before=${encodeURIComponent(opts.before)}`
        : "";
      return API.req(`/api/snapshots?${qs}`, { method: "DELETE" });
    },
    backfillSnapshots: (opts) => {
      const params = new URLSearchParams();
      if (opts && opts.cadence) params.set("cadence", opts.cadence);
      if (opts && opts.from) params.set("from", opts.from);
      if (opts && opts.to) params.set("to", opts.to);
      return API.req(`/api/snapshots/backfill?${params.toString()}`, { method: "POST" });
    },
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

    // Target / settings
    returns: (anchor, investible) => {
      const a = anchor || API.anchor();
      const inv = investible ? "&investible=1" : "";
      return API.req(`/api/returns?anchor=${a}${inv}`);
    },
    performance: (anchor, investible) => {
      const a = anchor || API.anchor();
      const inv = investible ? "&investible=1" : "";
      return API.req(`/api/performance?anchor=${a}${inv}`);
    },
    holdingsNearTarget: (anchor, bpsOverride) => {
      const a = anchor || API.anchor();
      const q = bpsOverride != null ? `&bps=${bpsOverride}` : "";
      return API.req(`/api/holdings-near-target?anchor=${a}${q}`);
    },
    getSettings: () => API.req("/api/settings"),
    setSetting: (key, value) =>
      API.req("/api/settings", { method: "PUT", json: { key, value } }),

    // Credenciales del user (BYMA, CAFCI). Los valores secretos no se devuelven.
    getCredentials: () => API.req("/api/credentials", { noCache: true }),
    updateCredentials: (data) => API.req("/api/credentials", { method: "PUT", json: data }),
    deleteCredentials: () => API.req("/api/credentials", { method: "DELETE" }),

    // Disparar loaders desde la app
    runByma: (tickers) => API.req("/api/loaders/byma", {
      method: "POST", json: tickers ? { tickers } : {},
    }),
    runCripto: (tickers) => API.req("/api/loaders/cripto", {
      method: "POST", json: tickers ? { tickers } : {},
    }),
    runCafci: (fecha) => API.req("/api/loaders/cafci", {
      method: "POST", json: fecha ? { fecha } : {},
    }),

    // Carga manual de tenencias viejas (saldos de apertura)
    cargaInicial: (payload) => API.req("/api/carga-inicial", {
      method: "POST", json: payload,
    }),

    // Audit log
    auditLog: (n = 100) => API.req(`/api/audit-log?n=${n}`, { noCache: true }),

    // Account management
    deleteAccount: () => fetch(`${API.base}/api/account`, {
      method: "DELETE",
      headers: {
        "Authorization": `Bearer ${API.token()}`,
        "X-Confirm-Delete": "yes",
      },
    }).then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(new Error(j.message || "delete failed")))),

    // Admin endpoints
    listUsers: () => API.req("/api/admin/users"),
    createUser: (data) => API.req("/api/admin/users", { method: "POST", json: data }),
    deleteUser: (id, deleteData) =>
      API.req(`/api/admin/users/${encodeURIComponent(id)}${deleteData ? '?delete_data=true' : ''}`,
              { method: "DELETE" }),
    seedDemo: (id) =>
      API.req(`/api/admin/users/${encodeURIComponent(id)}/seed-demo`,
              { method: "POST" }),
    switchUser: (target) =>
      API.req("/api/admin/switch", { method: "POST", json: { user_id: target } }),
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

  // Label de cuenta (Display Name negrita arriba, code chiquito abajo)
  // Si name === code, devuelve solo code (sin duplicar).
  function accountLabel(code, accountsRich) {
    const meta = accountsRich && accountsRich[code];
    const name = meta ? meta.name : null;
    if (!name || name === code) {
      return `<div style="font-weight: 600;">${escapeHtml(code)}</div>`;
    }
    return `
      <div style="font-weight: 600; line-height: 1.15;">${escapeHtml(name)}</div>
      <div class="muted" style="font-size: 11px; line-height: 1.15;">${escapeHtml(code)}</div>
    `;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[c]));
  }

  // Asset class → label "humano" para mostrar al lado del ticker.
  function assetClassLabel(cls) {
    const map = {
      "BOND_AR":      "Bonos AR",
      "BOND_CORP_AR": "ON Argentina",
      "BOND_US":      "Bonos US",
      "EQUITY_AR":    "Acciones AR",
      "EQUITY_US":    "Acciones US",
      "EQUITY_GLOBAL":"Acciones globales",
      "ETF":          "ETF",
      "REIT":         "REIT",
      "FCI":          "FCI",
      "CRYPTO":       "Cripto",
      "STABLECOIN":   "Stablecoin",
      "DERIVATIVE":   "Derivado",
      "COMMODITY":    "Commodity",
      "REAL_ESTATE":  "Inmueble",
      "PRIVATE":      "Privado",
      "CASH":         "Cash",
      "OTHER":        "Otro",
    };
    return map[cls] || (cls || "?");
  }

  // Render de ticker "limpio" para listas/holdings.
  // - "AAPL_AR" → AAPL  [AR]
  // - "AAPL_US" → AAPL  [US]
  // - FCI (asset_class='FCI'): usa el name del CAFCI (ej "Delta Ahorro Plus - Clase A")
  // - "BTC"     → BTC                (cuando asset_class='CRYPTO')
  // - resto     → ticker tal cual (sin badge)
  // Devuelve HTML con ticker-code + ticker-suffix.
  function tickerHtml(ticker, assetClass, name) {
    if (!ticker) return "";
    const t = String(ticker);
    // FCIs: priorizar el nombre largo de CAFCI si existe.
    if (assetClass === "FCI" && name && String(name).trim()) {
      return `<span class="ticker-code" style="font-weight:600;">${escapeHtml(name)}</span>`;
    }
    let base = t;
    let suffix = null;
    let suffixClass = "";
    if (/_AR$/i.test(t)) {
      base = t.replace(/_AR$/i, "");
      suffix = "AR";
      suffixClass = "ar";
    } else if (/_US$/i.test(t)) {
      base = t.replace(/_US$/i, "");
      suffix = "US";
      suffixClass = "us";
    } else if (assetClass === "EQUITY_AR") {
      suffix = "AR"; suffixClass = "ar";
    } else if (assetClass === "EQUITY_US") {
      suffix = "US"; suffixClass = "us";
    } else if (assetClass === "CRYPTO" || assetClass === "STABLECOIN") {
      // No mostramos badge — el ticker (BTC, ETH, USDT) es self-explanatory
      suffix = null;
    }
    let html = `<span class="ticker-code">${escapeHtml(base)}</span>`;
    if (suffix) {
      html += `<span class="ticker-suffix ${suffixClass}">${suffix}</span>`;
    }
    return html;
  }

  // -------- Cache de cuentas/especies/monedas --------
  let _meta = null;
  async function loadMeta() {
    if (_meta) return _meta;
    // Cuentas: traemos de /api/sheets/cuentas (las que vos definiste en Excel,
    // SIN las auto-creadas como caucion_pasivo_*).
    // Especies: traemos de /api/sheets/especies (todas las que cargaste, no
    // solo las que tenés en posición ahora).
    // Monedas: traemos de /api/sheets/monedas.
    try {
      const [cuentas, especies, monedas] = await Promise.all([
        API.listSheet("cuentas"),
        API.listSheet("especies"),
        API.listSheet("monedas"),
      ]);
      const allowed = (cuentas.items || [])
        .filter(c => c.Code &&
                     !["EXTERNAL", "OPENING_BALANCE",
                       "INTEREST_EXPENSE", "INTEREST_INCOME"]
                       .includes(c.Kind));
      const accounts = allowed.map(c => c.Code).sort();
      // accountsRich: {code → {name, kind, currency, institution}} para UI
      const accountsRich = {};
      for (const c of allowed) {
        accountsRich[c.Code] = {
          name: c.Nombre || c.Name || c.Code,
          kind: c.Kind || "",
          currency: c.Currency || c.Moneda || "",
          institution: c.Institution || c.Institucion || "",
        };
      }
      const tickers = (especies.items || [])
        .filter(e => e.Ticker).map(e => e.Ticker).sort();
      const currencies = (monedas.items || [])
        .filter(m => m.Code).map(m => m.Code).sort();
      _meta = { accounts, accountsRich, tickers, currencies };
      return _meta;
    } catch (e) {
      console.warn("loadMeta failed:", e);
      return { accounts: [], tickers: [], currencies: [] };
    }
  }
  function invalidateMeta() { _meta = null; }

  // -------- Router --------
  const routes = {};
  function route(path, handler) { routes[path] = handler; }
  function navigate(path) { window.location.hash = path; }

  function matchRoute(hash) {
    const raw = hash.replace(/^#/, "") || "/";
    const [path, qs] = raw.split("?", 2);
    const query = {};
    if (qs) {
      for (const pair of qs.split("&")) {
        const [k, v] = pair.split("=", 2);
        if (k) query[decodeURIComponent(k)] = v ? decodeURIComponent(v) : "";
      }
    }
    // Exact match first
    if (routes[path]) return { handler: routes[path], params: { ...query } };
    // Try patterns
    for (const pattern of Object.keys(routes)) {
      const re = new RegExp("^" + pattern.replace(/:(\w+)/g, "([^/]+)") + "$");
      const m = path.match(re);
      if (m) {
        const keys = (pattern.match(/:(\w+)/g) || []).map(k => k.slice(1));
        const params = { ...query };
        keys.forEach((k, i) => params[k] = decodeURIComponent(m[i + 1]));
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
      const msg = String(e.message || "");
      // Detectar el caso "Excel master no presente" y ofrecer bootstrap
      const isMissingMaster = /master no presente|no hay excel master/i.test(msg);
      root.innerHTML = isMissingMaster ? `
        <div class="cta-card" style="margin: 20px 16px;">
          <div class="cta-icon">📂</div>
          <div class="cta-title">Tu portfolio todavía está vacío</div>
          <div class="cta-desc">
            Empezá de cero o subí un Excel master que ya tengas
            (de un backup o de otra instalación).
          </div>
          <div class="cta-actions">
            <button class="btn primary" data-onclick="bootstrapMaster">
              ✨ Crear desde cero
            </button>
            <button class="btn ghost"
                    onclick="document.getElementById('xlsxUploadCta').click();">
              ⬆ Subir un Excel viejo
            </button>
          </div>
          <input type="file" id="xlsxUploadCta" accept=".xlsx,.xls"
                 style="display:none" onchange="window.uploadInitialExcel(event);">
        </div>
      ` : `<div class="empty"><div class="icon">⚠️</div><div>${escapeHtml(msg)}</div></div>`;
      attachListeners(root);
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
      "/funding": "#/funding/new",
      "/especies": "#/especies/new",
      "/monedas": "#/monedas/new",
      "/cuentas": "#/cuentas/new",
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

  // -------- Theme (dark / light) --------
  const THEME_KEY = "wm_theme";
  function getTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light") return saved;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark" : "light";
  }
  function applyTheme(t) {
    const html = document.documentElement;
    html.setAttribute("data-theme", t);
    // Sync theme-color meta para iOS standalone status bar
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", t === "dark" ? "#0E1628" : "#1F3864");
  }
  function themeIcon() {
    return getTheme() === "dark" ? "☀" : "☾";
  }
  // Aplicar inmediato (antes de que el usuario vea el shell)
  applyTheme(getTheme());

  // Reaccionar a cambios del sistema cuando no hay override del user
  if (window.matchMedia) {
    try {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
        if (!localStorage.getItem(THEME_KEY)) {
          applyTheme(e.matches ? "dark" : "light");
        }
      });
    } catch (_) {}
  }

  // -------- Actions --------
  window._actions = {
    toggleTheme() {
      const next = getTheme() === "dark" ? "light" : "dark";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
      // Re-render para refrescar el ícono del toggle
      try { render(); } catch (_) {}
    },
    async createTrade(data) {
      // Convertir tipos numéricos
      ["Qty", "Precio", "Comisión"].forEach(k => {
        if (data[k] !== null) data[k] = parseFloat(data[k]);
      });
      const res = await API.createRow("blotter", data);
      invalidateMeta();
      const fxFailed = res?.import_stats?.blotter_fx_failed;
      if (fxFailed && fxFailed.length) {
        const sample = fxFailed.slice(0, 3).map(f => `${f.ticker} (${f.from}→${f.to})`).join(", ");
        toast(`Trade agregado, pero faltó FX para ${fxFailed.length} fila(s): ${sample}. Cargá FX y refrescá.`, "error");
      } else {
        toast("Trade agregado ✓", "success");
      }
      navigate("/trades");
    },
    async updateTrade(data, form) {
      ["Qty", "Precio", "Comisión"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined) data[k] = parseFloat(data[k]);
      });
      const id = form.dataset.rowId;
      const res = await API.updateRow("blotter", id, data);
      invalidateMeta();
      const fxFailed = res?.import_stats?.blotter_fx_failed;
      if (fxFailed && fxFailed.length) {
        const sample = fxFailed.slice(0, 3).map(f => `${f.ticker} (${f.from}→${f.to})`).join(", ");
        toast(`Trade actualizado, pero faltó FX para ${fxFailed.length} fila(s): ${sample}.`, "error");
      } else {
        toast("Trade actualizado ✓", "success");
      }
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

    async createFunding(data) {
      ["Monto", "TNA", "Días"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined && data[k] !== "") {
          data[k] = parseFloat(data[k]);
        }
      });
      await API.createRow("funding", data);
      invalidateMeta();
      toast(`Funding ${data["Fund ID"] || ""} creado ✓`, "success");
      navigate("/funding");
    },
    async updateFunding(data, form) {
      ["Monto", "TNA", "Días"].forEach(k => {
        if (data[k] !== null && data[k] !== undefined && data[k] !== "") {
          data[k] = parseFloat(data[k]);
        }
      });
      const id = form.dataset.rowId;
      await API.updateRow("funding", id, data);
      invalidateMeta();
      toast("Funding actualizado ✓", "success");
      navigate("/funding");
    },
    async closeFunding(rowId) {
      const today = fmt.today();
      if (!confirm(`¿Cerrar este funding hoy (${today})?\nSi querés otra fecha, usá editar.`)) return;
      await API.updateRow("funding", rowId, {
        "Status": "CLOSED",
        "Fecha Fin": today,
      });
      invalidateMeta();
      toast("Funding cerrado ✓", "success");
      render();
    },
    async deleteFunding(id) {
      if (!confirm(`¿Borrar funding ${id}? Borrar elimina también sus events.`)) return;
      await API.deleteRow("funding", id);
      invalidateMeta();
      toast("Funding borrado", "success");
      render();
    },

    // Admin actions
    async createUserSubmit(data, form) {
      const userId = (data.user_id || "").trim().toLowerCase();
      if (!userId) { toast("user_id requerido", "error"); return; }
      try {
        const result = await API.createUser({
          user_id: userId,
          display_name: data.display_name || userId,
          is_admin: data.is_admin === "on" || data.is_admin === "1" || data.is_admin === true,
          token: "auto",
        });
        const url = result.url || window.location.origin;
        let msg = `User '${result.user_id}' creado!\n\nURL: ${url}\nToken:\n${result.token}\n\n` +
                  `📋 Compartilo con el amigo por un canal seguro.\n\n`;
        if (result.persistent) {
          msg += `✓ ${result.info}`;
        } else {
          msg += `⚠ ${result.warning}\n\n${result.wsgi_snippet || ""}`;
        }
        alert(msg);
        invalidateMeta();
        navigate("/admin");
      } catch (e) {
        toast(e.message, "error");
      }
    },
    async deleteUserAction(userId) {
      if (!confirm(`¿Borrar config del user '${userId}'?\n\n` +
                    `Los archivos del disk NO se borran (seguridad). El user dejará ` +
                    `de poder loguearse pero su data queda en inputs/${userId}/ y data/${userId}/.`)) return;
      try {
        await API.deleteUser(userId, false);
        toast(`User '${userId}' eliminado del config`, "success");
        render();
      } catch (e) {
        toast(e.message, "error");
      }
    },
    async seedDemoData(userId) {
      if (!confirm(`Sobreescribir datos del user '${userId}' con el dataset demo?\n\n` +
                    `Se hará backup del master actual antes. La data demo es fija ` +
                    `(misma cada vez).`)) return;
      try {
        const r = await API.seedDemo(userId);
        const stats = r.seed_stats || {};
        const summary = Object.entries(stats).map(([k,v]) => `${k}: ${v}`).join(", ");
        toast(`✓ Demo seedeado en '${userId}'. ${summary}`, "success");
        API._bustCache();
        invalidateMeta();
        render();
      } catch (e) { toast(e.message, "error"); }
    },

    async switchToUser(userId) {
      try {
        await API.switchUser(userId);
        if (userId) {
          toast(`Switch a '${userId}' (read-only)`, "info");
        } else {
          toast(`Volviste a tu user`, "success");
        }
        API._bustCache();
        invalidateMeta();
        navigate("/");
      } catch (e) {
        toast(e.message, "error");
      }
    },

    async setAlertDistance(form) {
      const fd = new FormData(form);
      const bps = parseFloat(fd.get("alert_distance_bps"));
      if (!isFinite(bps) || bps < 0 || bps > 10000) {
        toast("Valor inválido (0-10000 bps)", "error"); return;
      }
      try {
        await API.setSetting("alert_distance_bps", bps);
        toast(`Alert distance → ${bps} bps`, "success");
        API._bustCache();
        render();
      } catch (e) { toast(e.message, "error"); }
    },

    async bootstrapMaster() {
      try {
        toast("Creando tu portfolio...", "info");
        const r = await API.req("/api/account/bootstrap", { method: "POST" });
        if (r.already) {
          toast("Ya tenías master — refrescando", "info");
        } else {
          toast("✓ Portfolio creado. Empezá cargando cuentas.", "success");
        }
        invalidateMeta();
        API._bustCache();
        navigate("/welcome");
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    async refreshAll() {
      try {
        toast("Refrescando...", "info");
        API._bustCache();
        await API.refresh();
        invalidateMeta();
        toast("Refresh completado ✓", "success");
        render();
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },

    async saveCredentials(data) {
      // Filtrar fields vacíos: si el user dejó un campo en blanco, NO mandamos
      // null (eso borraría). Solo mandamos los que tienen valor nuevo.
      const payload = {};
      for (const [k, v] of Object.entries(data)) {
        if (v !== null && v !== undefined && String(v).trim() !== "") {
          payload[k] = v;
        }
      }
      if (Object.keys(payload).length === 0) {
        toast("No hay cambios para guardar", "info");
        return;
      }
      try {
        await API.updateCredentials(payload);
        toast("Credenciales guardadas ✓", "success");
        navigate("/credentials");
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    async deleteCredentials() {
      if (!confirm("¿Borrar todas las credenciales del broker? Vas a tener que volver a cargarlas.")) return;
      try {
        await API.deleteCredentials();
        toast("Credenciales borradas", "success");
        navigate("/credentials");
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    async testBroker(brokerId) {
      try {
        toast(`Probando ${brokerId}...`, "info");
        const r = await API.req(`/api/import/${brokerId}/test`,
                                  { method: "POST" });
        if (r.ok === false) {
          toast(`✗ ${r.error || 'Falló'}`, "error");
        } else {
          const detail = r.n_positions != null
            ? `${r.n_positions} posiciones detectadas`
            : (r.account_type || "OK");
          toast(`✓ ${brokerId}: ${detail}`, "success");
        }
      } catch (e) { toast(e.message, "error"); }
    },
    async applyImport(_data, form) {
      const fd = new FormData(form);
      const broker = fd.get("broker");
      const account = fd.get("account");
      const fecha = fd.get("fecha");
      const create_missing = fd.get("create_missing_assets") === "on";
      const checked = form.querySelectorAll("input[name=imp]:checked");
      const positions = [];
      for (const cb of checked) {
        const i = cb.value;
        const dataInput = form.querySelector(`input[name="data_${i}"]`);
        if (!dataInput) continue;
        try {
          positions.push(JSON.parse(dataInput.value));
        } catch (_) {}
      }
      if (positions.length === 0) {
        toast("Seleccioná al menos una posición", "error"); return;
      }
      try {
        toast(`Importando ${positions.length} posiciones...`, "info");
        const r = await API.req(`/api/import/${broker}/apply`, {
          method: "POST",
          json: { account, fecha, positions,
                  create_missing_assets: create_missing },
        });
        let msg = `✓ ${r.n_positions_written} posiciones importadas`;
        if (r.n_assets_created) msg += `, ${r.n_assets_created} assets creados`;
        toast(msg, "success");
        if ((r.warnings || []).length > 0) {
          setTimeout(() => toast(
            `${r.warnings.length} warnings — revisá audit log`, "info"
          ), 1500);
        }
        invalidateMeta();
        navigate("/");
      } catch (e) {
        toast(e.message, "error");
      }
    },
    async runByma() {
      try {
        toast("Bajando precios BYMA... (puede tardar 10-20s)", "info");
        const res = await API.runByma();
        toast(`✓ ${res.n_tickers} tickers refrescados`, "success");
        API._bustCache();
        render();
      } catch (e) {
        if (e.message.toLowerCase().includes("credenciales")) {
          if (confirm("Faltan credenciales BYMA. ¿Configurarlas ahora?")) {
            navigate("/credentials");
          }
        } else {
          toast(`Error: ${e.message}`, "error");
        }
      }
    },
    async runCripto() {
      try {
        toast("Bajando precios cripto desde CoinGecko...", "info");
        const res = await API.runCripto();
        let msg = `✓ ${res.n_prices} precios cripto refrescados`;
        if ((res.unknown_skipped || []).length) {
          msg += ` (skip: ${res.unknown_skipped.join(",")})`;
        }
        toast(msg, "success");
        API._bustCache();
        render();
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    async runCafci() {
      try {
        toast("Bajando VCP desde CAFCI... (puede tardar)", "info");
        const res = await API.runCafci();
        toast(`✓ FCIs refrescados (compartido a todos los users)`, "success");
        API._bustCache();
        render();
      } catch (e) {
        if (e.message.toLowerCase().includes("token")) {
          if (confirm("Falta el token CAFCI. ¿Configurarlo ahora?")) {
            navigate("/credentials");
          }
        } else if (e.message.toLowerCase().includes("superadmin")) {
          toast("Solo el superadmin puede correr CAFCI", "error");
        } else {
          toast(`Error: ${e.message}`, "error");
        }
      }
    },
    async submitCargaInicial(_data, form) {
      // Lee las posiciones del formulario y las manda a /api/carga-inicial.
      // Estructura esperada del form:
      //   account, fecha
      //   pos_<i>_ticker, pos_<i>_qty, pos_<i>_unit_price, pos_<i>_currency,
      //   pos_<i>_is_cash (checkbox), pos_<i>_asset_class, pos_<i>_name,
      //   pos_<i>_strategy
      const fd = new FormData(form);
      const account = (fd.get("account") || "").trim();
      const fecha = fd.get("fecha") || fmt.today();
      if (!account) { toast("Elegí una cuenta", "error"); return; }

      // Recolectar posiciones por índice
      const byIdx = {};
      for (const [k, v] of fd.entries()) {
        const m = k.match(/^pos_(\d+)_(.+)$/);
        if (!m) continue;
        const i = m[1], field = m[2];
        if (!byIdx[i]) byIdx[i] = {};
        byIdx[i][field] = v;
      }
      const positions = [];
      for (const i of Object.keys(byIdx).sort((a, b) => +a - +b)) {
        const p = byIdx[i];
        const ticker = (p.ticker || "").trim().toUpperCase();
        const qty = parseFloat(p.qty);
        if (!ticker || !qty || isNaN(qty)) continue;
        const isCash = !!p.is_cash;
        const ccy = (p.currency || "").trim().toUpperCase()
                      || (isCash ? ticker : "ARS");
        positions.push({
          ticker,
          qty,
          unit_price: p.unit_price && !isCash ? parseFloat(p.unit_price) : null,
          currency: ccy,
          is_cash: isCash,
          asset_class: (p.asset_class || "").trim() || (isCash ? "CASH" : "OTHER"),
          name: (p.name || "").trim() || ticker,
          strategy: (p.strategy || "").trim() || null,
        });
      }
      if (positions.length === 0) {
        toast("Agregá al menos una posición con ticker + qty", "error");
        return;
      }
      try {
        toast(`Cargando ${positions.length} saldos iniciales...`, "info");
        const res = await API.cargaInicial({
          account, fecha, positions, create_missing_assets: true,
        });
        let msg = `✓ ${res.n_positions_written} saldos cargados`;
        if (res.n_assets_created) msg += `, ${res.n_assets_created} assets nuevos`;
        if (res.n_asientos_generated) msg += ` (${res.n_asientos_generated} asientos)`;
        toast(msg, "success");
        if ((res.warnings || []).length) {
          setTimeout(() => toast(
            `${res.warnings.length} warnings — revisá audit log`, "info"
          ), 1500);
        }
        invalidateMeta();
        navigate("/");
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    addCargaInicialRow() {
      const tbody = document.getElementById("carga-positions");
      if (!tbody) return;
      const idx = tbody.children.length;
      const row = document.createElement("div");
      row.className = "card";
      row.style.cssText = "padding: 10px; margin-bottom: 8px;";
      row.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <b style="font-size:13px;">Posición ${idx + 1}</b>
          <button type="button" class="btn ghost" style="padding:2px 8px; font-size:11px;"
                  onclick="this.closest('.card').remove();">✕</button>
        </div>
        <div class="field-row">
          <div class="field">
            <label style="font-size:11px;">Ticker / moneda *</label>
            <input name="pos_${idx}_ticker" placeholder="AL30D / ARS / BTC" required
                   style="text-transform:uppercase;">
          </div>
          <div class="field" style="display:flex; align-items:center; gap:6px;">
            <label style="font-size:11px;">
              <input type="checkbox" name="pos_${idx}_is_cash" style="width:auto;"
                     onchange="
                       const card = this.closest('.card');
                       const isCash = this.checked;
                       card.querySelector('[name=pos_${idx}_unit_price]').disabled = isCash;
                       card.querySelector('[name=pos_${idx}_unit_price]').value = '';
                     ">
              Es cash (saldo en moneda)
            </label>
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label style="font-size:11px;">Cantidad *</label>
            <input type="number" step="any" name="pos_${idx}_qty" required>
          </div>
          <div class="field">
            <label style="font-size:11px;">Precio unitario</label>
            <input type="number" step="any" name="pos_${idx}_unit_price"
                   placeholder="cost basis (no cash)">
          </div>
          <div class="field">
            <label style="font-size:11px;">Moneda del precio</label>
            <input name="pos_${idx}_currency" placeholder="ARS / USB / USD"
                   style="text-transform:uppercase;">
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label style="font-size:11px;">Asset class (si es nuevo)</label>
            <select name="pos_${idx}_asset_class">
              <option value="">— autodetect —</option>
              <option value="BOND_AR">BOND_AR (bonos AR)</option>
              <option value="BOND_CORP_AR">BOND_CORP_AR (ON)</option>
              <option value="BOND_US">BOND_US (bonos US)</option>
              <option value="EQUITY_AR">EQUITY_AR (acciones AR)</option>
              <option value="EQUITY_US">EQUITY_US / CEDEAR</option>
              <option value="ETF">ETF</option>
              <option value="FCI">FCI</option>
              <option value="CRYPTO">CRYPTO</option>
              <option value="STABLECOIN">STABLECOIN</option>
              <option value="CASH">CASH</option>
              <option value="OTHER">OTHER</option>
            </select>
          </div>
          <div class="field">
            <label style="font-size:11px;">Nombre (si es nuevo)</label>
            <input name="pos_${idx}_name" placeholder="Bonar 2030">
          </div>
          <div class="field">
            <label style="font-size:11px;">Strategy</label>
            <select name="pos_${idx}_strategy">
              <option value="">—</option>
              <option value="BH">BH</option>
              <option value="TRADING">TRADING</option>
              <option value="CORE">CORE</option>
              <option value="FCI">FCI</option>
              <option value="CRYPTO">CRYPTO</option>
              <option value="CASH">CASH</option>
            </select>
          </div>
        </div>
      `;
      tbody.appendChild(row);
    },
    async updateDisplayName(data) {
      try {
        await API.req("/api/auth/me", { method: "PUT", json: data });
        toast("Nombre actualizado ✓", "success");
        render();
      } catch (e) { toast(e.message, "error"); }
    },
    async changePassword(data, form) {
      if (data.new_password !== data.confirm) {
        toast("Las contraseñas no coinciden", "error"); return;
      }
      try {
        await API.req("/api/auth/change-password", {
          method: "POST",
          json: { current_password: data.current_password,
                  new_password: data.new_password },
        });
        toast("Contraseña cambiada ✓", "success");
        form.reset();
        render();
      } catch (e) { toast(e.message, "error"); }
    },
    async resendVerify() {
      try {
        const r = await API.req("/api/auth/resend-verify", { method: "POST" });
        if (r.already_verified) {
          toast("Tu email ya está verificado", "info");
        } else {
          toast(`Te mandamos un email (${r.via})`, "success");
        }
      } catch (e) { toast(e.message, "error"); }
    },
    async revokeSession(prefix) {
      if (!confirm(`¿Revocar la sesión que empieza con ${prefix}…?`)) return;
      try {
        await API.req(`/api/auth/sessions/${encodeURIComponent(prefix)}`,
                       { method: "DELETE" });
        toast("Sesión revocada", "success");
        render();
      } catch (e) { toast(e.message, "error"); }
    },
    async revokeAllSessions() {
      if (!confirm("¿Revocar todas las otras sesiones? Tendrás que volver a loguearte en cada device.")) return;
      try {
        const r = await API.req("/api/auth/sessions", { method: "DELETE" });
        toast(`Revocadas ${r.revoked} sesiones`, "success");
        render();
      } catch (e) { toast(e.message, "error"); }
    },
    async toggleAdmin(arg) {
      const [userId, val] = arg.split("|");
      try {
        await API.req(`/api/superadmin/users/${encodeURIComponent(userId)}/admin`,
          { method: "PUT", json: { is_admin: val === "1" } });
        toast("Updated ✓", "success");
        render();
      } catch (e) { toast(e.message, "error"); }
    },
    async toggleSuper(arg) {
      const [userId, val] = arg.split("|");
      const isPromoting = val === "1";
      const msg = isPromoting
        ? `¿Convertir a "${userId}" en SUPERADMIN? Va a poder ver y gestionar todos los users.`
        : `¿Quitarle SUPERADMIN a "${userId}"?`;
      if (!confirm(msg)) return;
      try {
        await API.req(`/api/superadmin/users/${encodeURIComponent(userId)}/superadmin`,
          { method: "PUT", json: { is_superadmin: isPromoting } });
        toast("Updated ✓", "success");
        render();
      } catch (e) { toast(e.message, "error"); }
    },
    async deleteAuthUser(userId) {
      if (!confirm(`¿Borrar la cuenta auth de "${userId}"? Esto NO borra sus datos de wealth (xlsx, db) — para eso pediles que borren su propia cuenta.`)) return;
      try {
        await API.req(`/api/superadmin/users/${encodeURIComponent(userId)}`,
          { method: "DELETE" });
        toast("User borrado", "success");
        render();
      } catch (e) { toast(e.message, "error"); }
    },
    async deleteMyAccount() {
      const phrase = "BORRAR";
      const got = prompt(
        `Esto borra TODOS tus datos: Excel master, DB, credenciales, audit log y backups. ` +
        `Tu user_id queda libre (el admin puede reasignarlo).\n\n` +
        `Para confirmar, escribí "${phrase}":`
      );
      if (got !== phrase) {
        toast("Cancelado", "info");
        return;
      }
      try {
        const res = await API.deleteAccount();
        toast(`Borrado: ${(res.deleted || []).join(", ")}`, "success");
        setTimeout(() => API.logout(), 1500);
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    async resetSnapshots() {
      if (!confirm("¿Borrar TODOS los snapshots históricos? La equity curve arranca de cero. Hacelo si tu PN inicial está contaminado (ej. 0 o un parcial sin FX). El próximo refresh graba un snapshot limpio.")) return;
      try {
        const r = await API.deleteSnapshots({ all: true });
        toast(`Borrados ${r.deleted} snapshots ✓`, "success");
        await API.refresh();  // graba un snapshot limpio inmediatamente
        render();
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    },
    async backfillSnapshots() {
      const cadenceStr = prompt(
        "Reconstruir equity curve histórica calculando el PN en fechas pasadas a partir de tu historial de movimientos.\n\n" +
        "Cadencia (días entre snapshots, recomendado 7):", "7"
      );
      if (cadenceStr === null) return;
      const cadence = parseInt(cadenceStr, 10) || 7;
      const reset = confirm(
        "¿Borrar snapshots existentes antes de reconstruir?\n" +
        "OK = borrar y reconstruir desde cero (recomendado si está contaminado).\n" +
        "Cancelar = preservar snapshots actuales y solo agregar los faltantes."
      );
      try {
        toast("Reconstruyendo histórico...", "info");
        if (reset) await API.deleteSnapshots({ all: true });
        const r = await API.backfillSnapshots({ cadence });
        toast(`Backfill: ${r.n_snapshots_written} snapshots en ${r.n_dates_tried} fechas (${r.fecha_desde} → ${r.fecha_hasta}) ✓`, "success");
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
    async logout() {
      if (!confirm("¿Cerrar sesión y borrar token?")) return;
      // Best-effort: invalidar la sesión en el server (auth_db). No bloquea
      // si falla — el user igual va a quedar sin token.
      try {
        await API.req("/api/auth/logout", { method: "POST" });
      } catch (_) {}
      API.logout();
    },
  };

  // -------- Pages --------

  // /  Dashboard
  route("/", async () => {
    const view = API.view();  // 'all' | 'investible'
    let s, fills, nearTarget, meta, cashData, holdingsData, retData, cfg;
    try {
      [s, fills, nearTarget, meta, cashData, holdingsData, retData, cfg] = await Promise.all([
        API.summary(),
        API.realizedPnl(),
        API.holdingsNearTarget().catch(() => ({ alerts: [], n_alerts: 0, alert_distance_bps: 10 })),
        loadMeta().catch(() => ({ accountsRich: {} })),
        API.cash().catch(() => ({ items: [], by_currency: {}, total_anchor: 0 })),
        API.holdings(API.anchor()).catch(() => ({ items: [] })),
        API.returns(API.anchor(), view === "investible").catch(() => ({ returns: {} })),
        API.config().catch(() => ({ auth_user_display_name: null })),
      ]);
    } catch (e) {
      // Si la DB está vacía o falla el summary, ofrecer setup wizard
      const cfg = await API.config().catch(() => null);
      if (cfg && !cfg.xlsx_present) {
        // Sin Excel — redirigir al wizard
        setTimeout(() => navigate("/welcome"), 100);
        return `${headerWithBack("Bienvenido", "/")}<main><div class="loading"><div class="spinner"></div>Te llevo al onboarding...</div></main>`;
      }
      throw e;
    }
    // Si el summary devuelve 0 holdings y el Excel está vacío, sugerir wizard
    if (s.n_positions === 0 && (s.patrimonio_total || 0) === 0) {
      const cfg = await API.config().catch(() => null);
      if (cfg && cfg.xlsx_present) {
        // Tiene Excel pero está vacío — mostrar tip al wizard
      } else {
        setTimeout(() => navigate("/welcome"), 100);
        return `${headerWithBack("Bienvenido", "/")}<main><div class="loading"><div class="spinner"></div>Te llevo al onboarding...</div></main>`;
      }
    }

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

    // Performance chips (chico, al lado del PN)
    const periods = retData?.returns || {};
    const PERIOD_LABELS = [
      ["1d", "1d"], ["1w", "1s"], ["1m", "1m"],
      ["3m", "3m"], ["ytd", "YTD"], ["1y", "1a"],
    ];
    const perfChipsHtml = `
      <div class="perf-chips">
        ${PERIOD_LABELS.map(([key, label]) => {
          const r = periods[key];
          const pct = r?.return_pct;
          if (pct == null) {
            return `<span class="perf-chip"><span class="muted">${label}</span> <span class="muted">·</span></span>`;
          }
          const cls = pct > 0 ? "positive" : pct < 0 ? "negative" : "muted";
          const sign = pct > 0 ? "+" : "";
          return `<span class="perf-chip"><span class="muted">${label}</span> <span class="${cls}">${sign}${(pct * 100).toFixed(2)}%</span></span>`;
        }).join("")}
      </div>
    `;

    // Drilldowns: Activos por asset class + cuentas con saldo positivo, Pasivos por kind
    const assetsItems = (holdingsData.items || [])
      .filter(h => !h.is_liability && (h.mv_anchor || 0) > 0);
    const assetsByClass = {};
    for (const h of assetsItems) {
      const k = h.asset_class || "?";
      assetsByClass[k] = (assetsByClass[k] || 0) + (h.mv_anchor || 0);
    }
    const liabItems = (holdingsData.items || [])
      .filter(h => h.is_liability && Math.abs(h.mv_anchor || 0) > 0);

    // Display name del user (auth_user_display_name viene de /api/config)
    const userName = cfg?.auth_user_display_name || "";

    return `
      <div class="topbar">
        <h1>📊 Portfolio${userName ? ` · <span class="user-name">${escapeHtml(userName)}</span>` : ""}</h1>
        <div class="actions">
          <button class="theme-toggle" data-onclick="toggleTheme" title="Cambiar tema">${themeIcon()}</button>
          <button data-onclick="refreshAll" title="Refrescar">⟳</button>
        </div>
      </div>
      <main class="has-bottom-nav">
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
            ${perfChipsHtml}
          </div>
        </div>

        <div class="kpi-grid">
          <details class="kpi expandable">
            <summary>
              <div class="kpi-label">📈 Activos <span class="muted" style="font-size: 11px; font-weight: normal;">▾</span></div>
              <div class="kpi-value positive">${fmt.money(s.total_assets || 0)}</div>
              <div class="kpi-currency">${escapeHtml(API.anchor())}</div>
            </summary>
            <div class="drilldown">
              ${Object.entries(assetsByClass).sort((a,b) => b[1]-a[1]).map(([k, v]) => {
                const pct = (v / (s.total_assets || 1)) * 100;
                return `<div class="drill-row">
                  <span>${escapeHtml(k)}</span>
                  <span class="tabular">${fmt.money(v)} <span class="muted" style="font-size: 10px;">${pct.toFixed(0)}%</span></span>
                </div>`;
              }).join("")}
              <a href="#/holdings" class="drill-link">Ver todos los holdings →</a>
            </div>
          </details>
          <details class="kpi expandable">
            <summary>
              <div class="kpi-label">📉 Pasivos <span class="muted" style="font-size: 11px; font-weight: normal;">${liabItems.length > 0 ? "▾" : ""}</span></div>
              <div class="kpi-value negative">${fmt.money(s.total_liabilities || 0)}</div>
              <div class="kpi-currency">cauciones, tarjetas...</div>
            </summary>
            <div class="drilldown">
              ${liabItems.length === 0 ? '<div class="muted" style="text-align: center; padding: 8px;">Sin deudas</div>' :
                liabItems.sort((a,b) => Math.abs(b.mv_anchor||0) - Math.abs(a.mv_anchor||0)).map(h => `
                  <div class="drill-row">
                    <div style="display:flex; flex-direction: column;">
                      <span>${accountLabel(h.account, meta?.accountsRich)}</span>
                    </div>
                    <span class="tabular negative">${fmt.money(Math.abs(h.mv_anchor||0))}</span>
                  </div>
                `).join("")
              }
              <a href="#/pasivos" class="drill-link">Detalle de pasivos →</a>
            </div>
          </details>
        </div>
        ${s.patrimonio_no_invertible && Math.abs(s.patrimonio_no_invertible) > 0.01 ? `
          <div class="card compact muted" style="margin-bottom: 16px; font-size: 12px;">
            ℹ Cash no-invertible (reserva no declarada): ${fmt.money(s.patrimonio_no_invertible)} ${API.anchor()}
          </div>
        ` : ""}

        ${nearTarget && nearTarget.n_alerts > 0 ? `
          <section>
            <h2>🎯 Cerca del target / stop <span class="muted" style="font-weight: normal; font-size: 12px;">(${nearTarget.alert_distance_bps} bps)</span></h2>
            <div class="card" style="border-left: 4px solid var(--yellow);">
              ${nearTarget.alerts.map(a => {
                const isTP = a.alert === "TP";
                const ref = isTP ? a.target_price : a.stop_loss_price;
                const dist = isTP ? a.dist_to_target_bps : a.dist_to_stop_bps;
                const sign = isTP ? (dist >= 0 ? "✅" : "→") : (dist <= 0 ? "🛑" : "→");
                const distStr = (dist != null) ? `${dist >= 0 ? "+" : ""}${dist.toFixed(0)} bps` : "?";
                return `<a href="#/holdings" style="display:flex; justify-content:space-between; padding: 8px 0; border-bottom: 1px solid var(--border); text-decoration: none; color: inherit;">
                  <div>
                    <div style="font-weight: 600;">${sign} ${escapeHtml(a.asset)}
                      <span class="tag" style="background: ${isTP ? '#E0F2E9' : '#FEE2E2'}; color: ${isTP ? '#10A66B' : '#DC2626'};">${a.alert}</span>
                    </div>
                    <div class="muted" style="font-size: 12px;">
                      ${escapeHtml(a.account)} · MP ${fmt.money(a.market_price, 4)} → ${isTP ? 'TP' : 'SL'} ${fmt.money(ref, 4)} ${escapeHtml(a.target_currency || a.native_currency)}
                    </div>
                  </div>
                  <div class="tabular ${dist >= 0 ? (isTP ? 'positive' : 'negative') : (isTP ? '' : 'positive')}" style="align-self: center;">${distStr}</div>
                </a>`;
              }).join("")}
            </div>
          </section>
        ` : ""}

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
                const ar = meta?.accountsRich?.[k];
                const ccy = ar?.currency || "";
                return `<div style="display:flex; justify-content:space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); gap: 8px;">
                  <div style="flex: 1; min-width: 0;">${accountLabel(k, meta?.accountsRich)}</div>
                  ${ccy ? `<span class="tag" style="font-size:10px;">${escapeHtml(ccy)}</span>` : ""}
                  <span class="tabular ${v < 0 ? 'negative' : ''}" style="white-space: nowrap;">${fmt.money(v)} <span class="muted" style="font-size: 11px;">${pct.toFixed(1)}%</span></span>
                </div>`;
              }).join("")
            }
          </div>
        </section>

        ${cashData && cashData.items && cashData.items.length > 0 ? `
          <section>
            <details>
              <summary style="cursor: pointer; padding: 0; list-style: none;">
                <h2 style="display: inline-flex; align-items: center; gap: 8px;">
                  💵 Cash total
                  <span class="tabular" style="font-weight: 600; color: var(--text);">${fmt.money(cashData.total_anchor || 0)} ${escapeHtml(API.anchor())}</span>
                  <span class="muted" style="font-size: 12px; font-weight: normal;">(toca para ver cuentas)</span>
                </h2>
              </summary>
              <div class="card" style="margin-top: 8px;">
                ${(() => {
                  const items = (cashData.items || []).slice().sort((a,b) => (b.mv_anchor||0) - (a.mv_anchor||0));
                  return items.map(c => `
                    <div style="display:flex; justify-content:space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); gap: 8px;">
                      <div style="flex: 1; min-width: 0;">${accountLabel(c.account, meta?.accountsRich)}</div>
                      ${c.account_kind ? `<span class="tag" style="font-size: 10px;">${escapeHtml(c.account_kind.toLowerCase())}</span>` : ""}
                      <span class="tag" style="font-size: 10px;">${escapeHtml(c.currency)}</span>
                      <div class="right" style="white-space: nowrap;">
                        <div class="tabular" style="font-weight: 500;">${fmt.money(c.qty)}</div>
                        <div class="muted" style="font-size: 11px;">${fmt.money(c.mv_anchor || 0, 2)} ${escapeHtml(API.anchor())}</div>
                      </div>
                    </div>
                  `).join("");
                })()}
                <a href="#/cash" class="btn ghost full" style="margin-top: 8px;">Ver detalle completo →</a>
              </div>
            </details>
          </section>
        ` : ""}

        ${(() => {
          // Cómo vienen rindiendo los activos individuales (top 3 ganadores
          // / 3 perdedores). Usa unrealized_pct de holdings — return desde
          // que se incorporó (price actual vs avg cost).
          const ranked = (holdingsData.items || [])
            .filter(h => !h.is_cash && !h.is_liability && h.unrealized_pct != null
                         && h.qty && h.qty !== 0)
            .map(h => ({...h}))
            .sort((a, b) => (b.unrealized_pct || 0) - (a.unrealized_pct || 0));
          if (ranked.length === 0) return "";
          const winners = ranked.slice(0, 3);
          const losers = ranked.slice(-3).reverse().filter(h =>
            !winners.some(w => w.account === h.account && w.asset === h.asset));
          const renderRow = (h) => {
            const ret = h.unrealized_pct;
            const cls = ret > 0 ? "positive" : ret < 0 ? "negative" : "muted";
            const sign = ret > 0 ? "+" : "";
            return `
              <a href="#/asset/${encodeURIComponent(h.asset)}?account=${encodeURIComponent(h.account)}" style="display:flex; justify-content:space-between; padding: 8px 0; border-bottom: 1px solid var(--border); text-decoration:none; color:inherit;">
                <div style="min-width:0;">
                  <div style="font-weight:600;">${escapeHtml(h.asset)} <span class="muted" style="font-size:11px;">›</span></div>
                  <div class="muted" style="font-size:11px;">${escapeHtml(h.account)}</div>
                </div>
                <div class="right">
                  <div class="tabular ${cls}">${sign}${(ret * 100).toFixed(2)}%</div>
                  <div class="sub muted tabular" style="font-size:11px;">${fmt.money(h.unrealized_pnl_native, 0)} ${escapeHtml(h.native_currency || "")}</div>
                </div>
              </a>
            `;
          };
          return `
            <section>
              <h2>📊 Cómo van mis activos</h2>
              <div class="card">
                ${winners.length > 0 ? `
                  <div class="muted" style="font-size:11px; padding:4px 0;">🏆 Mejores</div>
                  ${winners.map(renderRow).join("")}
                ` : ""}
                ${losers.length > 0 ? `
                  <div class="muted" style="font-size:11px; padding:8px 0 4px;">📉 Peores</div>
                  ${losers.map(renderRow).join("")}
                ` : ""}
                <a href="#/asset-performance" class="drill-link">Ver todos los activos →</a>
              </div>
            </section>
          `;
        })()}

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
            <a href="#/holdings" class="btn ghost full">📋 Holdings</a>
            <a href="#/cash" class="btn ghost full">💵 Cash</a>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px;">
            <a href="#/leverage" class="btn ghost full">⚡ Leverage</a>
            <a href="#/calculator" class="btn ghost full">🧮 Calculator</a>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px;">
            <a href="#/cotizaciones" class="btn ghost full">💹 Cotizaciones</a>
            <a href="#/funding" class="btn ghost full">💰 Funding</a>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px;">
            <a href="#/pasivos" class="btn ghost full">📉 Pasivos</a>
            <a href="#/calendar" class="btn ghost full">📅 Calendario</a>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px;">
            <a href="#/journal" class="btn ghost full">📔 Journal</a>
            <a href="#/transferencias" class="btn ghost full">🔄 Transferencias</a>
          </div>
          <a href="#/performance" class="btn ghost full" style="margin-top: 8px;">📊 Performance (TWR / MWR)</a>
        </section>
      </main>
      ${bottomNav("/")}
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
        ${items.length === 0 ? `
          <div class="cta-card">
            <div class="cta-icon">📈</div>
            <div class="cta-title">Aún no cargaste trades</div>
            <div class="cta-desc">
              Cargá tu primera operación de compra/venta. Después la app
              mantiene tus tenencias, costo promedio y PnL realizado.
            </div>
            <div class="cta-actions">
              <a href="#/trades/new" class="btn primary">+ Nuevo trade</a>
              <a href="#/welcome" class="btn ghost">¿Cómo arranco?</a>
            </div>
          </div>
        ` : `<div class="list">${items.map(r => `
            <a class="list-item" href="#/trades/${encodeURIComponent(r.row_id || "")}/edit">
              <div class="meta">
                <div class="meta-line1">
                  ${r.Side === "BUY" ? "🟢" : "🔴"} ${escapeHtml(r.Side || "")}
                  ${tickerHtml(r.Ticker, "")}
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
      ${bottomNav("/trades")}
    `;
  });

  // /trades/new
  route("/trades/new", async () => {
    const meta = await loadMeta();
    setTimeout(() => attachTradeCashPreview(meta), 50);
    return `
      ${headerWithBack("Nuevo trade", "/trades")}
      <main>
        <form data-action="createTrade">
          ${tradeFormFields({}, meta)}
          <div id="trade-cash-preview" class="card compact" style="margin: 8px 0; font-size: 13px; display: none;"></div>
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
      </main>
    `;
  });

  // /trades/:id/edit
  route("/trades/:id/edit", async ({ id }) => {
    const meta = await loadMeta();
    const row = await API.getSheetRow("blotter", id);
    setTimeout(() => attachTradeCashPreview(meta), 50);
    return `
      ${headerWithBack("Editar trade", "/trades")}
      <main>
        <form data-action="updateTrade" data-row-id="${escapeHtml(id)}">
          ${tradeFormFields(row, meta)}
          <div id="trade-cash-preview" class="card compact" style="margin: 8px 0; font-size: 13px; display: none;"></div>
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteTrade" data-arg="${escapeHtml(id)}">
          🗑 Borrar trade
        </button>
      </main>
    `;
  });

  // Cash preview helper para el form de trade.
  // Se monta DESPUÉS de que el HTML del form esté en el DOM.
  // Wire onchange en Cuenta Cash, Side, Qty, Precio, Moneda Trade →
  // calcula costo y saldo resultante, y los muestra en #trade-cash-preview.
  async function attachTradeCashPreview(meta) {
    const div = document.getElementById("trade-cash-preview");
    if (!div) return;
    let cashData;
    try {
      cashData = await API.cash(API.anchor());
    } catch (e) {
      div.innerHTML = `<span class="muted">Sin datos de cash (${escapeHtml(e.message)})</span>`;
      div.style.display = "block";
      return;
    }
    // Index: {account+currency: qty}
    const balByKey = {};
    for (const c of (cashData.items || [])) {
      balByKey[`${c.account}|${c.currency}`] = c.qty;
    }
    function getField(name) {
      const el = document.querySelector(`[name="${name}"]`);
      return el ? el.value : "";
    }
    function update() {
      const cuentaCash = getField("Cuenta Cash");
      const side = getField("Side");
      const qty = parseFloat(getField("Qty")) || 0;
      const precio = parseFloat(getField("Precio")) || 0;
      const moneda = getField("Moneda Trade");
      if (!cuentaCash || !moneda) {
        div.style.display = "none";
        return;
      }
      const ar = meta?.accountsRich?.[cuentaCash];
      const accountName = (ar?.name && ar.name !== cuentaCash) ? ar.name : cuentaCash;
      const balance = balByKey[`${cuentaCash}|${moneda}`] || 0;
      const costo = qty * precio;
      // BUY: cuenta cash pierde costo. SELL: gana.
      const sign = (side === "BUY") ? -1 : (side === "SELL") ? +1 : 0;
      const resultado = balance + sign * costo;
      const apalancado = resultado < 0 && side === "BUY";
      const colorRes = apalancado ? "var(--orange, #F59E0B)" :
                        (resultado < 0 ? "var(--red, #DC2626)" : "var(--green, #10A66B)");
      const arrow = side === "BUY" ? "🔻" : side === "SELL" ? "🔺" : "↔";
      const opLabel = side === "BUY" ? "Costo" : side === "SELL" ? "Ingreso" : "Movimiento";
      div.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: baseline; gap: 8px;">
          <div style="flex: 1; min-width: 0;">
            <div class="muted" style="font-size: 11px;">Saldo actual de</div>
            <div style="font-weight: 600;">${escapeHtml(accountName)} <span class="tag" style="font-size: 10px;">${escapeHtml(moneda)}</span></div>
          </div>
          <div class="tabular" style="font-weight: 600;">${fmt.money(balance, 2)}</div>
        </div>
        ${costo > 0 ? `
          <div style="display: flex; justify-content: space-between; padding-top: 4px; border-top: 1px dashed var(--border); margin-top: 6px;">
            <span class="muted">${arrow} ${opLabel} (${fmt.money(qty, 4)} × ${fmt.money(precio, 4)})</span>
            <span class="tabular">${sign === -1 ? "−" : sign === 1 ? "+" : ""}${fmt.money(costo, 2)} ${escapeHtml(moneda)}</span>
          </div>
          <div style="display: flex; justify-content: space-between; padding-top: 4px; margin-top: 4px; align-items: center;">
            <span style="font-weight: 600;">Saldo resultante</span>
            <span class="tabular" style="font-weight: 700; color: ${colorRes};">
              ${fmt.money(resultado, 2)} ${escapeHtml(moneda)}
              ${apalancado ? '<br><span class="tag" style="background: #FEF3C7; color: #B45309; font-size: 9px;">🔻 apalancado</span>' : ""}
            </span>
          </div>
        ` : ""}
      `;
      div.style.display = "block";
    }
    // Wire listeners en todos los campos relevantes
    ["Cuenta Cash", "Side", "Qty", "Precio", "Moneda Trade"].forEach(name => {
      const el = document.querySelector(`[name="${name}"]`);
      if (el) el.addEventListener("input", update);
      if (el) el.addEventListener("change", update);
    });
    update();
  }

  // /gastos
  // ====================================================================
  // /flows — vista combinada de gastos + ingresos con toggle
  // ====================================================================
  route("/flows", async () => {
    const tab = sessionStorage.getItem("flows_tab") || "gastos";  // 'gastos' | 'ingresos'
    const [gastos, ingresos] = await Promise.all([
      API.listSheet("gastos").catch(() => ({ items: [] })),
      API.listSheet("ingresos").catch(() => ({ items: [] })),
    ]);
    const gastosItems = (gastos.items || []).filter(r => r.Concepto || r.Monto).reverse();
    const ingresosItems = (ingresos.items || []).filter(r => r.Concepto || r.Monto).reverse();

    const gastosTotal = gastosItems.reduce((s, r) => s + (parseFloat(r.Monto) || 0), 0);
    const ingresosTotal = ingresosItems.reduce((s, r) => s + (parseFloat(r.Monto) || 0), 0);

    const items = tab === "ingresos" ? ingresosItems : gastosItems;
    const newRoute = tab === "ingresos" ? "/ingresos/new" : "/gastos/new";
    const editPath = tab === "ingresos" ? "/ingresos" : "/gastos";

    return `
      <div class="topbar">
        <button onclick="window.history.length>1 ? window.history.back() : window.location.hash='/'">‹ Atrás</button>
        <h1>💸 Flujos</h1>
        <div></div>
      </div>
      <main class="has-bottom-nav">
        <div class="toggle-pill" style="margin-bottom: 14px; width: 100%;">
          <button onclick="window._setFlowsTab('gastos')" class="${tab === 'gastos' ? 'active' : ''}" style="flex:1;">
            💸 Gastos <span class="muted" style="font-size: 11px;">(${gastosItems.length})</span>
          </button>
          <button onclick="window._setFlowsTab('ingresos')" class="${tab === 'ingresos' ? 'active' : ''}" style="flex:1;">
            💰 Ingresos <span class="muted" style="font-size: 11px;">(${ingresosItems.length})</span>
          </button>
        </div>

        <div class="kpi-grid">
          <div class="kpi">
            <div class="kpi-label">${tab === 'ingresos' ? 'Total ingresos' : 'Total gastos'}</div>
            <div class="kpi-value ${tab === 'ingresos' ? 'positive' : 'negative'}">
              ${tab === 'ingresos' ? '+ ' : '- '}${fmt.money(tab === 'ingresos' ? ingresosTotal : gastosTotal)}
            </div>
            <div class="kpi-currency muted">${items.length > 0 ? 'todas las monedas, sin convertir' : ''}</div>
          </div>
        </div>

        ${items.length === 0 ?
          emptyState(`Sin ${tab}`, `Tocá + para agregar`) :
          `<div class="list">${items.map(r => `
            <a class="list-item" href="#${editPath}/${encodeURIComponent(r.row_id || '')}/edit">
              <div class="meta">
                <div class="meta-line1">${escapeHtml(r.Concepto || '(sin concepto)')}</div>
                <div class="meta-line2">
                  ${escapeHtml(fmt.date(r.Fecha))} · ${escapeHtml(r.Categoría || r.Categoria || '—')}
                  ${r.Tipo ? ' · ' + escapeHtml(r.Tipo) : ''}
                </div>
              </div>
              <div class="right">
                <div class="amount ${tab === 'ingresos' ? 'positive' : 'negative'}">
                  ${tab === 'ingresos' ? '+ ' : '- '}${fmt.money(r.Monto)}
                </div>
                <div class="sub">${escapeHtml(r.Moneda || '')} · ${escapeHtml(r['Cuenta Destino'] || '')}</div>
              </div>
            </a>
          `).join('')}</div>`
        }

        <a href="#${newRoute}" class="fab" title="Nuevo ${tab === 'ingresos' ? 'ingreso' : 'gasto'}">+</a>
      </main>
      ${bottomNav('/flows')}
    `;
  });

  window._setFlowsTab = function (tab) {
    sessionStorage.setItem("flows_tab", tab);
    render();
  };

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
  // /calendar — próximos 60 días: cupones, cierres, vencimientos
  // ====================================================================
  route("/calendar", async () => {
    const data = await API.req("/api/calendar?days=60");
    const events = data.events || [];

    // Agrupar por mes
    const byMonth = {};
    events.forEach(e => {
      const month = e.fecha.slice(0, 7);  // YYYY-MM
      if (!byMonth[month]) byMonth[month] = [];
      byMonth[month].push(e);
    });

    return `
      ${headerWithBack("📅 Calendario", "/")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Próximos 60 días: vencimientos de bonos, cierres y vencimientos de
          tarjetas, cauciones que vencen, ingresos/gastos recurrentes.
        </div>

        ${events.length === 0 ? '<div class="card muted" style="margin-top:12px">Sin eventos próximos</div>' :
          Object.keys(byMonth).sort().map(m => {
            const monthName = new Date(m + "-01").toLocaleDateString("es-AR",
              { year: "numeric", month: "long" });
            return `
              <section style="margin-top: 14px;">
                <h2>${escapeHtml(monthName)} (${byMonth[m].length})</h2>
                <div class="list">
                  ${byMonth[m].map(e => `
                    <div class="list-item" style="align-items: flex-start;">
                      <div style="font-size: 22px; margin-right: 8px;">${escapeHtml(e.icon)}</div>
                      <div class="meta">
                        <div class="meta-line1">${escapeHtml(e.title)}</div>
                        <div class="meta-line2">${escapeHtml(e.subtitle)}</div>
                      </div>
                      <div class="right">
                        <div class="amount tabular" style="font-size: 12px;">${escapeHtml(e.fecha)}</div>
                        ${e.amount ? `<div class="sub muted tabular">${fmt.money(e.amount, 0)} ${escapeHtml(e.currency || "")}</div>` : ""}
                      </div>
                    </div>
                  `).join("")}
                </div>
              </section>
            `;
          }).join("")
        }
      </main>
    `;
  });

  // ====================================================================
  // /journal — diario de trading: stats por Strategy/Setup
  // ====================================================================
  route("/journal", async () => {
    const [trades, fillsResp] = await Promise.all([
      API.listSheet("blotter"),
      API.realizedPnl(),
    ]);

    const tradeById = {};
    (trades.items || []).forEach(t => {
      if (t["Trade ID"]) tradeById[String(t["Trade ID"]).trim()] = t;
    });

    // Agrupar fills por estrategia (heurística: matchear con trade del blotter)
    const fills = fillsResp.fills || [];
    const byStrat = {};
    const noStrat = [];

    fills.forEach(fl => {
      // Match por ticker + cuenta + fecha_compra (NO por precio, porque
      // trade.Precio puede estar en moneda Trade ≠ fill.precio_compra que
      // está en moneda nativa post-conversión FX)
      const fechaCompra = String(fl.fecha_compra || "").slice(0, 10);
      const matchTrades = (trades.items || []).filter(t =>
        t.Ticker === fl.asset && t.Cuenta === fl.account &&
        String(t["Trade Date"] || "").slice(0, 10) === fechaCompra
      );
      const strat = matchTrades.length > 0 && matchTrades[0].Strategy
        ? matchTrades[0].Strategy
        : "(sin clasificar)";
      if (!byStrat[strat]) {
        byStrat[strat] = { strat, fills: [], total_pnl: 0, n_winners: 0,
                            n_losers: 0, currencies: new Set() };
      }
      byStrat[strat].fills.push(fl);
      byStrat[strat].total_pnl += fl.pnl_realizado;
      byStrat[strat].currencies.add(fl.currency);
      if (fl.pnl_realizado > 0) byStrat[strat].n_winners++;
      else if (fl.pnl_realizado < 0) byStrat[strat].n_losers++;
    });

    const stratList = Object.values(byStrat).map(s => ({
      ...s,
      n_trades: s.fills.length,
      winrate: (s.n_winners + s.n_losers) > 0
        ? s.n_winners / (s.n_winners + s.n_losers) : 0,
      avg_pnl: s.fills.length > 0 ? s.total_pnl / s.fills.length : 0,
      currencies: Array.from(s.currencies).join(", "),
    })).sort((a, b) => b.total_pnl - a.total_pnl);

    return `
      ${headerWithBack("📔 Trading journal", "/")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Performance agrupada por <b>Strategy</b> (campo del blotter).
          Te muestra qué tipos de trade funcionan mejor para vos. Cargá el
          campo Strategy en cada trade del blotter (BREAKOUT, MEAN_REV,
          BH, TRADING, EVENT_DRIVEN, etc).
        </div>

        ${stratList.length === 0 ? emptyState("Sin trades cerrados aún", "Vendé al menos una posición y volvé") :
          stratList.map(s => `
            <div class="card" style="margin-top: 12px;
                 border-left: 4px solid ${s.total_pnl > 0 ? 'var(--green)' : 'var(--red)'};">
              <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                <h3 style="font-size: 16px;">${escapeHtml(s.strat)}</h3>
                <span class="tabular ${s.total_pnl > 0 ? 'positive' : 'negative'}" style="font-weight: 700; font-size: 16px;">
                  ${fmt.money(s.total_pnl, 0)} ${escapeHtml(s.currencies)}
                </span>
              </div>
              <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; font-size: 12px;">
                <div><span class="muted">Trades:</span> <b>${s.n_trades}</b></div>
                <div><span class="muted">Win:</span> <b class="positive">${s.n_winners}</b></div>
                <div><span class="muted">Loss:</span> <b class="negative">${s.n_losers}</b></div>
                <div><span class="muted">WR:</span> <b>${(s.winrate * 100).toFixed(0)}%</b></div>
              </div>
              <div style="margin-top: 6px; font-size: 12px;">
                <span class="muted">Avg PnL/trade:</span>
                <b class="tabular ${s.avg_pnl > 0 ? 'positive' : 'negative'}">${fmt.money(s.avg_pnl, 0)}</b>
              </div>
            </div>
          `).join("")
        }

        ${stratList.length > 0 ? `
          <div class="card compact muted" style="margin-top: 16px; font-size: 11px;">
            💡 Tip: para refinar tu sistema, fijate qué estrategia tiene
            mejor expectancy = winrate × avg_winner − loss_rate × |avg_loser|.
            Doblá la apuesta en lo que funciona, evitá lo que no.
          </div>
        ` : ""}
      </main>
    `;
  });

  // ====================================================================
  // /calculator — simulador de trade apalancado (what-if)
  // ====================================================================
  route("/calculator", async () => {
    return `
      ${headerWithBack("🧮 Calculator", "/")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Simulá una operación apalancada antes de mandarla. Te dice cuánto
          capital propio necesitás, costo financiero al cierre, breakeven %
          y P&L objetivo a un precio target.
        </div>

        <form id="calcForm" style="margin-top: 12px;">
          <h2 style="font-size: 14px; color: var(--muted); text-transform: uppercase; margin: 12px 0 6px;">Trade</h2>
          <div class="field-row">
            ${inputField("Ticker", "ticker", "TXMJ9", "text", { placeholder: "ej AL30D" })}
            <div class="field">
              <label>Side</label>
              <select name="side">
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
              </select>
            </div>
          </div>
          <div class="field-row">
            ${inputField("Qty", "qty", "250000000", "number", { required: true })}
            ${inputField("Precio entrada", "entry_price", "0.80", "number", { required: true })}
          </div>
          ${inputField("Precio target (salida)", "exit_price", "0.835", "number", { required: true })}

          <h2 style="font-size: 14px; color: var(--muted); text-transform: uppercase; margin: 16px 0 6px;">Aforo (capital propio)</h2>
          <div class="field-row">
            ${inputField("% Aforo del activo", "aforo_pct", "0.85", "number",
                          { placeholder: "0..1 — ej 0.85 = 85%" })}
            <div class="field">
              <label>Modo</label>
              <select name="mode">
                <option value="cap_propio">% sobre activo</option>
                <option value="margin">Margin × multiplier</option>
              </select>
            </div>
          </div>

          <h2 style="font-size: 14px; color: var(--muted); text-transform: uppercase; margin: 16px 0 6px;">Caución</h2>
          <div class="field-row">
            ${inputField("TNA caución", "tna", "0.24", "number",
                          { placeholder: "0.24 o 24" })}
            ${inputField("Días", "days", "4", "number", { required: true })}
          </div>

          <button type="submit" class="btn primary full" style="margin-top: 12px;">📊 Calcular</button>
        </form>

        <div id="calcResult" style="margin-top: 16px;"></div>
      </main>
    `;
  });

  // Listener para calc form (delega — re-attached on every render)
  document.addEventListener("submit", (e) => {
    if (e.target && e.target.id === "calcForm") {
      e.preventDefault();
      computeCalculator(e.target);
    }
  });

  function computeCalculator(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    const qty = parseFloat(data.qty) || 0;
    const entry = parseFloat(data.entry_price) || 0;
    const exit = parseFloat(data.exit_price) || 0;
    let aforo = parseFloat(data.aforo_pct) || 0;
    if (aforo > 1) aforo = aforo / 100;
    let tna = parseFloat(data.tna) || 0;
    if (tna > 1.5) tna = tna / 100;
    const days = parseInt(data.days) || 0;

    const notional = qty * entry;
    // Capital propio: lo que TÚ ponés. Lo que pedís de caución es (1-aforo) del notional
    // En el modelo BYMA: aforo es lo que el activo cubre como garantía.
    // Si comprás 100 con aforo 85%, podés pedir prestado 85 (caución) y poner 15 propio
    let capitalPropio, montoCaucion;
    if (data.mode === "margin") {
      // Modo IBKR: multiplier = 1/aforo
      const multiplier = aforo > 0 ? 1 / aforo : 1;
      capitalPropio = notional / multiplier;
      montoCaucion = notional - capitalPropio;
    } else {
      // BYMA: capital propio = (1-aforo) * notional
      montoCaucion = notional * aforo;
      capitalPropio = notional - montoCaucion;
    }

    const interestCost = montoCaucion * tna * days / 365;
    const grossPnl = (exit - entry) * qty * (data.side === "SELL" ? -1 : 1);
    const netPnl = grossPnl - interestCost;
    const breakevenMove = capitalPropio > 0
      ? (interestCost / qty)  // movimiento mínimo en precio para cubrir interés
      : 0;
    const breakevenPct = entry > 0 ? breakevenMove / entry : 0;
    const targetMovePct = entry > 0 ? (exit - entry) / entry : 0;
    const roi = capitalPropio > 0 ? netPnl / capitalPropio : 0;
    const roiAnnual = days > 0 ? roi * 365 / days : 0;

    document.getElementById("calcResult").innerHTML = `
      <div class="card" style="border-left: 4px solid var(--navy);">
        <h2 style="font-size: 16px; margin-bottom: 12px;">Resultado de la simulación</h2>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; font-size: 14px;">
          <div><span class="muted">Notional:</span> <b class="tabular">${fmt.money(notional, 0)}</b></div>
          <div><span class="muted">Capital propio:</span> <b class="tabular">${fmt.money(capitalPropio, 0)}</b></div>
          <div><span class="muted">Caución (deuda):</span> <b class="tabular">${fmt.money(montoCaucion, 0)}</b></div>
          <div><span class="muted">Apalancamiento:</span> <b>${capitalPropio > 0 ? (notional / capitalPropio).toFixed(2) + "x" : "—"}</b></div>
        </div>

        <hr style="border: none; border-top: 1px solid var(--border); margin: 12px 0;">

        <h3 style="font-size: 13px; color: var(--muted); text-transform: uppercase; margin-bottom: 8px;">Costos y PnL</h3>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; font-size: 14px;">
          <div><span class="muted">Interés ${days}d (TNA ${(tna * 100).toFixed(2)}%):</span> <b class="tabular negative">−${fmt.money(interestCost, 0)}</b></div>
          <div><span class="muted">Move target:</span> <b class="${targetMovePct > 0 ? 'positive' : 'negative'}">${(targetMovePct * 100).toFixed(2)}%</b></div>
          <div><span class="muted">P&L bruto:</span> <b class="tabular ${grossPnl > 0 ? 'positive' : 'negative'}">${fmt.money(grossPnl, 0)}</b></div>
          <div><span class="muted">P&L neto:</span> <b class="tabular ${netPnl > 0 ? 'positive' : 'negative'}">${fmt.money(netPnl, 0)}</b></div>
        </div>

        <hr style="border: none; border-top: 1px solid var(--border); margin: 12px 0;">

        <div class="kpi" style="background: ${netPnl >= 0 ? '#E8F5E9' : '#FFEBEE'}; padding: 14px;">
          <div class="kpi-label">ROI sobre capital propio</div>
          <div class="kpi-value ${roi > 0 ? 'positive' : 'negative'}">${(roi * 100).toFixed(2)}%</div>
          <div class="kpi-currency">en ${days} días · anualizado ${(roiAnnual * 100).toFixed(1)}%</div>
        </div>

        <div class="card compact" style="background: #FFF8E1; padding: 8px; margin-top: 10px; font-size: 12px;">
          🎯 <b>Break-even:</b> el precio tiene que subir al menos
          <b>${fmt.money(breakevenMove, 4)}</b> (${(breakevenPct * 100).toFixed(3)}%)
          para cubrir el costo de la caución.
        </div>

        <div class="muted" style="font-size: 11px; margin-top: 10px;">
          ⚠ Simulación. No considera comisiones, slippage, ni movimientos
          intermedios. El aforo BYMA real de cada activo está en la hoja
          aforos del Excel.
        </div>
      </div>
    `;
  }

  // ====================================================================
  // /leverage — operaciones apalancadas (trade + caución vinculada)
  // ====================================================================
  route("/leverage", async () => {
    const [fundings, trades, fillsResp] = await Promise.all([
      API.listSheet("funding"),
      API.listSheet("blotter"),
      API.realizedPnl(),
    ]);

    // Index trades by Trade ID
    const tradeById = {};
    (trades.items || []).forEach(t => {
      if (t["Trade ID"]) tradeById[String(t["Trade ID"]).trim()] = t;
    });

    const fills = fillsResp.fills || [];

    const ops = (fundings.items || [])
      .filter(f => f["Linked Trade ID"])
      .map(f => buildLeverageOp(f, tradeById, fills));

    // Sort: OPEN first, then by Fecha Inicio desc
    ops.sort((a, b) => {
      if (a.status !== b.status) return a.status === "OPEN" ? -1 : 1;
      return (b.fecha_inicio || "").localeCompare(a.fecha_inicio || "");
    });

    const opens = ops.filter(o => o.status !== "CLOSED");
    const closed = ops.filter(o => o.status === "CLOSED");

    // KPIs agregados de operaciones abiertas
    const totalMonto = opens.reduce((s, o) => s + o.monto, 0);
    const totalCapitalPropio = opens.reduce((s, o) => s + o.capital_propio, 0);
    const totalInterestAccrued = opens.reduce((s, o) => s + o.interest_accrued, 0);
    const totalNetPnl = opens.reduce((s, o) => s + o.net_pnl, 0);

    return `
      ${headerWithBack("⚡ Leverage", "/")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Operaciones apalancadas: cauciones (TOMA) vinculadas a un trade
          del blotter vía <b>Linked Trade ID</b>. Te muestra capital
          propio expuesto, intereses devengados al día de hoy, P&L del
          trade y ROI sobre tu margen real.
        </div>

        ${opens.length > 0 ? `
          <div class="kpi-grid" style="margin-top: 12px;">
            <div class="kpi">
              <div class="kpi-label">Apalancado total</div>
              <div class="kpi-value">${fmt.money(totalMonto, 0)}</div>
              <div class="kpi-currency">caución TOMA · ${opens[0]?.moneda || ''}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Capital propio</div>
              <div class="kpi-value">${fmt.money(totalCapitalPropio, 0)}</div>
              <div class="kpi-currency">tu plata expuesta</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Costo financiero hoy</div>
              <div class="kpi-value negative">−${fmt.money(totalInterestAccrued, 0)}</div>
              <div class="kpi-currency">interés devengado</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">P&L neto (estim)</div>
              <div class="kpi-value ${totalNetPnl >= 0 ? 'positive' : 'negative'}">${fmt.money(totalNetPnl, 0)}</div>
              <div class="kpi-currency">trade − interés</div>
            </div>
          </div>
        ` : ""}

        <section style="margin-top: 14px;">
          <h2>Operaciones abiertas (${opens.length})</h2>
          ${opens.length === 0 ?
            '<div class="card muted">Sin operaciones apalancadas en curso. Vinculá una caución a un trade poniéndole su Trade ID en "Linked Trade ID" desde la página /funding.</div>' :
            opens.map(o => leverageOpCard(o)).join("")
          }
        </section>

        ${closed.length > 0 ? `
          <section style="margin-top: 16px;">
            <h2>Operaciones cerradas (${closed.length})</h2>
            ${closed.slice(0, 10).map(o => leverageOpCard(o)).join("")}
          </section>
        ` : ""}

        <div class="card compact muted" style="margin-top: 16px; font-size: 11px;">
          ⚠ Nota: el matching trade↔caución usa el campo "Linked Trade ID"
          de la hoja funding. Si tu trade tiene legs separadas (T0001-A buy,
          T0001-B sell), poné el Trade ID de la BUY en Linked Trade ID — el
          P&L se trae del fill cerrado correspondiente.
        </div>
      </main>
    `;
  });

  function buildLeverageOp(f, tradeById, fills) {
    const linkedId = String(f["Linked Trade ID"] || "").trim();
    const trade = tradeById[linkedId];

    // Parsear datos del funding
    const monto = parseFloat(f.Monto) || 0;
    let tna = parseFloat(f.TNA) || 0;
    if (tna > 1.5) tna = tna / 100;

    // Días corridos (al día de hoy si OPEN; al Fecha Fin si CLOSED)
    const today = new Date();
    const start = parseISODate(f["Fecha Inicio"]);
    let daysElapsed = 0, daysTotal = 0;
    if (start) {
      const refEnd = f["Fecha Fin"] ? parseISODate(f["Fecha Fin"]) : today;
      daysElapsed = Math.max(0, Math.floor((Math.min(refEnd, today) - start) / 86400000));
      daysTotal = parseInt(f.Días) || daysElapsed;
    }

    const interestAccrued = monto * tna * daysElapsed / 365;
    const interestFinal = monto * tna * daysTotal / 365;

    // Trade notional (qty * precio en moneda nativa)
    const tradeQty = trade ? parseFloat(trade.Qty) || 0 : 0;
    const tradePrice = trade ? parseFloat(trade.Precio) || 0 : 0;
    const tradeNotional = tradeQty * tradePrice;

    // Capital propio = lo que pusiste de tu bolsillo = notional - monto cauci
    const capitalPropio = Math.max(0, tradeNotional - monto);

    // P&L del trade: matcheamos fills por ticker + cuenta + fecha_compra
    // (NO por precio, porque trade.Precio puede estar en otra moneda que
    // fill.precio_compra después de la conversión FX del importer)
    let tradePnl = 0;
    let pnlCurrency = trade ? trade["Moneda Trade"] : null;
    if (trade) {
      const tradeDate = String(trade["Trade Date"] || "").slice(0, 10);
      const matched = fills.filter(fl =>
        fl.account === trade.Cuenta &&
        fl.asset === trade.Ticker &&
        String(fl.fecha_compra || "").slice(0, 10) === tradeDate
      );
      tradePnl = matched.reduce((s, fl) => s + fl.pnl_realizado, 0);
      if (matched.length > 0) pnlCurrency = matched[0].currency;
    }

    const netPnl = tradePnl - interestAccrued;
    const roi = capitalPropio > 0 ? netPnl / capitalPropio : null;

    // ROI anualizado simple
    const roiAnnual = (roi && daysElapsed > 0) ? roi * 365 / daysElapsed : null;

    return {
      fund_id: f["Fund ID"],
      row_id: f.row_id,
      linked_trade_id: linkedId,
      tipo: f.Tipo,
      subtipo: f.Subtipo,
      cuenta: f.Cuenta,
      moneda: f.Moneda,
      fecha_inicio: f["Fecha Inicio"],
      fecha_fin: f["Fecha Fin"],
      monto, tna, days_elapsed: daysElapsed, days_total: daysTotal,
      interest_accrued: interestAccrued,
      interest_final: interestFinal,
      status: f.Status || "OPEN",
      trade, trade_notional: tradeNotional, capital_propio: capitalPropio,
      trade_pnl: tradePnl,
      net_pnl: netPnl,
      pnl_currency: pnlCurrency,
      roi, roi_annual: roiAnnual,
    };
  }

  function parseISODate(s) {
    if (!s) return null;
    const d = new Date(String(s).slice(0, 10));
    return isNaN(d) ? null : d;
  }

  function leverageOpCard(o) {
    const isOpen = o.status !== "CLOSED";
    const sideBadge = (o.trade && o.trade.Side === "BUY") ? "🟢 BUY" :
                       (o.trade && o.trade.Side === "SELL") ? "🔴 SELL" : "?";
    const pnlClass = o.net_pnl > 0 ? "positive" : o.net_pnl < 0 ? "negative" : "";
    const roiPct = o.roi !== null ? (o.roi * 100).toFixed(2) + "%" : "—";
    const roiAnnualPct = o.roi_annual !== null ? (o.roi_annual * 100).toFixed(1) + "%" : "—";

    return `
      <div class="card" style="margin-bottom: 10px; border-left: 4px solid ${isOpen ? 'var(--yellow)' : 'var(--green)'};">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <div>
            <b>${escapeHtml(o.tipo)} · ${escapeHtml(o.subtipo)}</b>
            <span class="muted" style="font-size: 12px;">· ${escapeHtml(o.fund_id || "")}</span>
            ${isOpen ? '<span class="tag warn" style="margin-left: 6px;">OPEN</span>' : '<span class="tag" style="margin-left: 6px;">cerrada</span>'}
          </div>
          ${isOpen ? `
            <button class="btn" style="padding: 4px 10px; font-size: 11px; background: var(--green); color: white;"
                    data-onclick="closeFunding" data-arg="${escapeHtml(o.row_id)}">
              Cerrar caución
            </button>
          ` : ""}
        </div>

        ${o.trade ? `
          <div style="background: #FAFBFC; padding: 8px; border-radius: 6px; margin-bottom: 8px; font-size: 13px;">
            <div><b>Trade vinculado:</b> ${sideBadge} ${escapeHtml(o.trade.Ticker)} ·
                 ${fmt.money(o.trade.Qty, 0)} @ ${fmt.money(o.trade.Precio, 4)} ${escapeHtml(o.trade["Moneda Trade"])}</div>
            <div class="muted" style="font-size: 11px;">${escapeHtml(o.linked_trade_id)} · ${escapeHtml(o.trade["Trade Date"] || "")} · ${escapeHtml(o.cuenta)}</div>
          </div>
        ` : `
          <div class="card compact danger" style="background: #FFEBEE; padding: 8px; font-size: 12px; margin-bottom: 8px;">
            ⚠ Trade <code>${escapeHtml(o.linked_trade_id)}</code> no encontrado en blotter.
          </div>
        `}

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; font-size: 13px;">
          <div><span class="muted">Caución:</span> <b class="tabular">${fmt.money(o.monto, 0)} ${escapeHtml(o.moneda)}</b></div>
          <div><span class="muted">TNA:</span> <b>${(o.tna * 100).toFixed(2)}%</b></div>
          <div><span class="muted">Notional trade:</span> <b class="tabular">${fmt.money(o.trade_notional, 0)}</b></div>
          <div><span class="muted">Capital propio:</span> <b class="tabular">${fmt.money(o.capital_propio, 0)}</b></div>
          <div><span class="muted">Días corridos:</span> <b>${o.days_elapsed}d</b> ${o.days_total !== o.days_elapsed ? "/" + o.days_total : ""}</div>
          <div><span class="muted">Interés acum:</span> <b class="tabular negative">−${fmt.money(o.interest_accrued, 0)}</b></div>
          <div><span class="muted">P&L trade:</span> <b class="tabular ${o.trade_pnl > 0 ? 'positive' : o.trade_pnl < 0 ? 'negative' : ''}">${fmt.money(o.trade_pnl, 0)} ${escapeHtml(o.pnl_currency || "")}</b></div>
          <div><span class="muted">P&L neto:</span> <b class="tabular ${pnlClass}">${fmt.money(o.net_pnl, 0)}</b></div>
        </div>

        <hr style="border: none; border-top: 1px solid var(--border); margin: 8px 0;">

        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <span class="muted" style="font-size: 12px;">ROI sobre capital propio:</span>
            <b style="font-size: 16px; margin-left: 6px;" class="${o.roi > 0 ? 'positive' : o.roi < 0 ? 'negative' : ''}">${roiPct}</b>
            ${o.roi_annual !== null ? `<span class="muted" style="font-size: 11px;"> · anualiz. ${roiAnnualPct}</span>` : ""}
          </div>
          <div>
            <a href="#/funding/${encodeURIComponent(o.row_id)}/edit" style="font-size: 12px;">editar →</a>
          </div>
        </div>
      </div>
    `;
  }

  // ====================================================================
  // /funding — cauciones, pases, préstamos
  // ====================================================================
  route("/funding", async () => {
    const data = await API.listSheet("funding");
    const items = (data.items || []).filter(r => r["Fund ID"] || r.Monto);
    // Ordenar: OPEN primero, después por fecha inicio desc
    items.sort((a, b) => {
      const sa = a.Status === "OPEN" ? 0 : 1;
      const sb = b.Status === "OPEN" ? 0 : 1;
      if (sa !== sb) return sa - sb;
      return (b["Fecha Inicio"] || "").localeCompare(a["Fecha Inicio"] || "");
    });
    const opens = items.filter(r => r.Status !== "CLOSED");
    const closed = items.filter(r => r.Status === "CLOSED");
    return `
      ${headerWithBack("💰 Funding (cauciones)", "/")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Cauciones tomadas (TOMA = pedís plata) y colocadas (COLOCA = prestás plata).
          Las TOMA crean pasivo automáticamente; las COLOCA crean crédito por cobrar.
        </div>

        <section style="margin-top: 12px;">
          <h2>Abiertas (${opens.length})</h2>
          ${opens.length === 0 ? '<div class="card muted">Sin funding abierto</div>' :
            `<div class="list">${opens.map(r => fundingItemCard(r)).join("")}</div>`
          }
        </section>

        ${closed.length > 0 ? `
          <section style="margin-top: 16px;">
            <h2>Cerradas recientes (${closed.length})</h2>
            <div class="list">${closed.slice(0, 10).map(r => fundingItemCard(r)).join("")}</div>
          </section>
        ` : ""}
      </main>
    `;
  });

  function fundingItemCard(r) {
    const isOpen = r.Status !== "CLOSED";
    const tipo = r.Tipo || "";
    const subtipo = r.Subtipo || "";
    const tipoColor = tipo === "TOMA" ? "negative" : "positive";
    const interes = (Number(r.Monto) || 0) * (Number(r.TNA) || 0) * (Number(r.Días) || 0) / 365.0;
    return `
      <a class="list-item" href="#/funding/${encodeURIComponent(r.row_id || "")}/edit"
         style="align-items: flex-start;">
        <div class="meta">
          <div class="meta-line1">
            <span class="${tipoColor}">${escapeHtml(tipo)}</span>
            ${escapeHtml(subtipo)}
            ${isOpen ? '<span class="tag warn">OPEN</span>' : '<span class="tag">cerrada</span>'}
          </div>
          <div class="meta-line2">
            ${escapeHtml(r["Fund ID"] || "(sin id)")} · ${escapeHtml(r.Cuenta || "")}
            ${r["Linked Trade ID"] ? ' · 🔗 ' + escapeHtml(r["Linked Trade ID"]) : ""}
          </div>
          <div class="meta-line2 muted">
            ${escapeHtml(fmt.date(r["Fecha Inicio"]))}
            ${r["Fecha Fin"] ? " → " + escapeHtml(fmt.date(r["Fecha Fin"])) : ""}
            ${r.Días ? " · " + r.Días + "d" : ""}
            ${r.TNA ? " · TNA " + (Number(r.TNA) * 100).toFixed(2) + "%" : ""}
          </div>
        </div>
        <div class="right">
          <div class="amount tabular">${fmt.money(r.Monto, 0)}</div>
          <div class="sub muted">${escapeHtml(r.Moneda || "")}</div>
          ${interes > 0 ? `<div class="sub muted tabular">int ~${fmt.money(interes, 0)}</div>` : ""}
          ${isOpen ? `
            <button class="btn" style="margin-top: 4px; padding: 4px 10px; font-size: 11px; background: var(--green); color: white;"
                    onclick="event.preventDefault(); event.stopPropagation(); _actions.closeFunding('${escapeHtml(r.row_id)}')">
              Cerrar hoy
            </button>
          ` : ""}
        </div>
      </a>
    `;
  }

  route("/funding/new", async () => {
    const meta = await loadMeta();
    return `
      ${headerWithBack("Nueva caución/funding", "/funding")}
      <main>
        <form data-action="createFunding">
          ${fundingFormFields({ Status: "OPEN", Tipo: "TOMA", Subtipo: "CAUCION" }, meta, false)}
          <button type="submit" class="btn primary full">Guardar</button>
        </form>
        <div class="card compact muted" style="margin-top: 12px; font-size: 12px;">
          💡 <b>Tip</b>: si esta caución cubre un trade del blotter, ponele
          su Trade ID en "Linked Trade ID". El TNA podés ponerlo como decimal
          (0.24) o porcentaje (24); el server normaliza.
        </div>
      </main>
    `;
  });

  route("/funding/:id/edit", async ({ id }) => {
    const meta = await loadMeta();
    const row = await API.getSheetRow("funding", id);
    return `
      ${headerWithBack("Editar funding", "/funding")}
      <main>
        <form data-action="updateFunding" data-row-id="${escapeHtml(id)}">
          ${fundingFormFields(row, meta, true)}
          <button type="submit" class="btn primary full">Guardar cambios</button>
        </form>
        <button class="btn danger full" style="margin-top:12px"
                data-onclick="deleteFunding" data-arg="${escapeHtml(id)}">🗑 Borrar funding</button>
      </main>
    `;
  });

  // ====================================================================
  // /holdings — todas las tenencias desagregadas
  // ====================================================================
  route("/holdings", async () => {
    const [data, meta] = await Promise.all([
      API.holdings(API.anchor()),
      loadMeta().catch(() => ({ accountsRich: {} })),
    ]);
    const all = data.items || [];

    // Filtros guardados en sessionStorage
    const showCash = sessionStorage.getItem("hold_cash") !== "0";
    const showLiab = sessionStorage.getItem("hold_liab") === "1";
    const search = (sessionStorage.getItem("hold_search") || "").toLowerCase();

    let items = all.slice();
    if (!showCash) items = items.filter(h => !h.is_cash);
    if (!showLiab) items = items.filter(h => !h.is_liability);
    if (search) {
      items = items.filter(h =>
        (h.asset || "").toLowerCase().includes(search) ||
        (h.account || "").toLowerCase().includes(search) ||
        (h.asset_class || "").toLowerCase().includes(search) ||
        (h.name || "").toLowerCase().includes(search)
      );
    }

    // Sort desc por |mv_anchor|
    items.sort((a, b) => Math.abs(b.mv_anchor || 0) - Math.abs(a.mv_anchor || 0));

    const totalAbs = items.reduce(
      (acc, h) => acc + Math.abs(h.mv_anchor || 0), 0
    ) || 1;

    return `
      ${headerWithBack("📋 Holdings", "/")}
      <main>
        <div class="card compact" style="margin-bottom: 12px;">
          <input type="search" id="holdSearch" placeholder="🔍 Buscar (ticker, cuenta, clase)..."
                 value="${escapeHtml(search)}"
                 style="width: 100%; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px;">
          <div style="display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;">
            <button class="btn ghost" style="padding: 6px 12px; font-size: 12px;"
                    onclick="toggleHoldFilter('hold_cash', this)">
              ${showCash ? "✓" : "✗"} Cash
            </button>
            <button class="btn ghost" style="padding: 6px 12px; font-size: 12px;"
                    onclick="toggleHoldFilter('hold_liab', this)">
              ${showLiab ? "✓" : "✗"} Pasivos
            </button>
            <span class="muted" style="font-size: 12px; align-self: center; margin-left: auto;">
              ${items.length} de ${all.length}
            </span>
          </div>
        </div>

        ${items.length === 0 ? emptyState("Sin posiciones que matcheen", "Cambiá los filtros") :
          `<div class="list">${items.map((h, idx) => {
            const pct = (Math.abs(h.mv_anchor || 0) / totalAbs) * 100;
            const isLiab = h.is_liability;
            const isCash = h.is_cash;
            const unr = h.unrealized_pnl_native;
            const unrPct = h.unrealized_pct;
            const tagBits = [];
            if (isCash) tagBits.push('<span class="tag">cash</span>');
            if (isLiab) tagBits.push('<span class="tag danger">deuda</span>');
            if (!h.investible) tagBits.push('<span class="tag warn">no-inv</span>');
            if (h.price_fallback) tagBits.push('<span class="tag warn">px*</span>');
            // Target / Stop info (solo no-cash)
            const dTP = h.dist_to_target_bps;
            const dSL = h.dist_to_stop_bps;
            const targetLine = (h.target_price || h.stop_loss_price) ? `
              <div class="meta-line2 tabular" style="margin-top: 2px; font-size: 11px;">
                ${h.target_price != null ? `🎯 ${fmt.money(h.target_price, 4)}${dTP != null ? ` <span class="${dTP >= 0 ? 'positive' : 'muted'}">(${dTP >= 0 ? '+' : ''}${dTP.toFixed(0)}bp)</span>` : ''}` : ""}
                ${h.target_price && h.stop_loss_price ? " · " : ""}
                ${h.stop_loss_price != null ? `🛑 ${fmt.money(h.stop_loss_price, 4)}${dSL != null ? ` <span class="${dSL <= 0 ? 'negative' : 'muted'}">(${dSL >= 0 ? '+' : ''}${dSL.toFixed(0)}bp)</span>` : ''}` : ""}
              </div>
            ` : "";
            const isClickable = !isCash && !isLiab;
            const clickHref = isClickable
              ? `#/asset/${encodeURIComponent(h.asset)}?account=${encodeURIComponent(h.account)}`
              : null;
            const wrapOpen = clickHref
              ? `<a href="${clickHref}" class="list-item" style="align-items: flex-start; text-decoration:none; color:inherit;">`
              : `<div class="list-item" style="align-items: flex-start;">`;
            const wrapClose = clickHref ? `</a>` : `</div>`;
            return `
              ${wrapOpen}
                <div class="meta">
                  <div class="meta-line1">
                    <span style="font-weight: 700;">#${idx + 1}</span>
                    ${tickerHtml(h.asset, h.asset_class, h.name)}
                    ${tagBits.join(" ")}
                    ${isClickable ? '<span class="muted" style="font-size:11px;">›</span>' : ""}
                  </div>
                  <div class="meta-line2" style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                    <span style="display: inline-block;">${accountLabel(h.account, meta.accountsRich)}</span>
                    <span class="muted">· ${escapeHtml(assetClassLabel(h.asset_class))}${(h.asset_class !== "FCI" && h.name && h.name !== h.asset) ? ' · ' + escapeHtml(h.name) : ""}</span>
                  </div>
                  <div class="meta-line2 tabular" style="margin-top: 4px;">
                    ${fmt.money(h.qty, 4)} ${escapeHtml(h.native_currency)}
                    ${!isCash ? `@ ${fmt.money(h.market_price, 4)}` : ""}
                  </div>
                  ${targetLine}
                </div>
                <div class="right">
                  <div class="amount tabular ${(h.mv_anchor || 0) < 0 ? 'negative' : ''}">${fmt.money(h.mv_anchor)}</div>
                  <div class="sub muted">${escapeHtml(API.anchor())} · ${pct.toFixed(1)}%</div>
                  ${(unr !== null && unr !== undefined && !isCash) ? `
                    <div class="sub tabular ${unr > 0 ? 'positive' : unr < 0 ? 'negative' : ''}" style="margin-top:2px">
                      ${unr > 0 ? '+' : ''}${fmt.money(unr, 0)} ${escapeHtml(h.native_currency)}
                      ${unrPct !== null ? ` (${(unrPct * 100).toFixed(1)}%)` : ""}
                    </div>
                  ` : ""}
                </div>
              ${wrapClose}
            `;
          }).join("")}</div>`
        }

        <div class="card compact muted" style="margin-top: 16px; font-size: 12px;">
          Suma de |MV|: ${fmt.money(totalAbs)} ${escapeHtml(API.anchor())}.
          Posiciones con <b>px*</b> tienen precio fallback (sin cotización
          fresca). Subí precios con <code>python sync.py --skip-loaders</code>.
        </div>
      </main>
      ${bottomNav("/")}
    `;
  });

  // /asset/:ticker — detalle histórico de un activo
  route("/asset/:ticker", async (params) => {
    const ticker = params.ticker;
    const account = params.account || null;
    let data;
    try {
      data = await API.assetHistory(ticker, account);
    } catch (e) {
      return `${headerWithBack("Activo", "/holdings")}
        <main><div class="card danger">${escapeHtml(e.message)}</div></main>`;
    }
    const evo = data.evolution || [];
    const movs = data.movements || [];

    // SVG sparkline del precio en moneda nativa
    function sparklinePrice(points) {
      if (!points || points.length < 2) return "";
      const w = 360, h = 110, pad = 6;
      const vals = points.map(p => p.price);
      const min = Math.min(...vals), max = Math.max(...vals);
      const range = (max - min) || 1;
      const stepX = (w - pad * 2) / (points.length - 1);
      const coords = points.map((p, i) => {
        const x = pad + i * stepX;
        const y = h - pad - ((p.price - min) / range) * (h - pad * 2);
        return [x, y];
      });
      const path = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
      const last = coords[coords.length - 1];
      // Marcar puntos donde hubo trades (overlay verde compra / rojo venta)
      const tradeDates = new Map();
      for (const m of movs) {
        const isBuy = (m.qty || 0) > 0;
        tradeDates.set(m.fecha, isBuy ? "buy" : "sell");
      }
      const markers = points.map((p, i) => {
        const k = tradeDates.get(p.fecha);
        if (!k) return "";
        const [x, y] = coords[i];
        const c = k === "buy" ? "#10A66B" : "#DC2626";
        return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" fill="${c}" stroke="white" stroke-width="1"/>`;
      }).join("");
      return `
        <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%; height:110px; display:block;">
          <path d="${path}" fill="none" stroke="#1F3864" stroke-width="2"/>
          ${markers}
          <circle cx="${last[0]}" cy="${last[1]}" r="3" fill="#1F3864"/>
        </svg>
        <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-top:4px;">
          <span>${escapeHtml(points[0].fecha)}</span>
          <span>${escapeHtml(points[points.length - 1].fecha)}</span>
        </div>
        <div class="muted" style="font-size:11px; margin-top:4px;">
          <span style="color:#10A66B;">●</span> compra ·
          <span style="color:#DC2626;">●</span> venta
        </div>
      `;
    }

    const retCls = data.return_pct == null ? "muted"
      : data.return_pct > 0 ? "positive" : "negative";
    const retSign = data.return_pct > 0 ? "+" : "";
    const ccy = data.native_currency || "";

    return `
      ${headerWithBack(`📈 ${escapeHtml(data.ticker)}`, "/holdings")}
      <main>
        <div class="card" style="margin-bottom:12px;">
          <div style="display:flex; justify-content:space-between; align-items:baseline;">
            <div>
              <div style="font-weight:700; font-size:18px;">${tickerHtml(data.ticker, data.asset_class)}</div>
              <div class="muted" style="font-size:12px;">
                ${escapeHtml(data.name || "")} ${data.asset_class ? "· " + escapeHtml(assetClassLabel(data.asset_class)) : ""}
              </div>
            </div>
            <div class="tabular ${retCls}" style="text-align:right;">
              <div style="font-size:18px; font-weight:700;">
                ${data.return_pct != null ? `${retSign}${(data.return_pct * 100).toFixed(2)}%` : "—"}
              </div>
              <div class="muted" style="font-size:11px;">return desde compra</div>
            </div>
          </div>
        </div>

        <div class="kpi-grid" style="margin-bottom:12px;">
          <div class="kpi">
            <div class="kpi-label">Desde</div>
            <div class="kpi-value" style="font-size:16px;">${escapeHtml(data.first_purchase_date || "—")}</div>
            <div class="kpi-currency muted">${data.days_held != null ? data.days_held + " días" : ""}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Qty actual</div>
            <div class="kpi-value tabular" style="font-size:16px;">${fmt.money(data.current_qty, 4)}</div>
            <div class="kpi-currency muted">${escapeHtml(ccy)}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Avg cost</div>
            <div class="kpi-value tabular" style="font-size:16px;">${fmt.money(data.avg_cost, 4)}</div>
            <div class="kpi-currency muted">${escapeHtml(ccy)}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">Precio actual</div>
            <div class="kpi-value tabular" style="font-size:16px;">${fmt.money(data.current_price, 4)}</div>
            <div class="kpi-currency muted">${escapeHtml(ccy)}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">PnL no realizado</div>
            <div class="kpi-value tabular ${data.unrealized_pnl_native > 0 ? 'positive' : data.unrealized_pnl_native < 0 ? 'negative' : ''}" style="font-size:16px;">
              ${fmt.money(data.unrealized_pnl_native, 0)}
            </div>
            <div class="kpi-currency muted">${escapeHtml(ccy)}</div>
          </div>
          <div class="kpi">
            <div class="kpi-label">PnL realizado</div>
            <div class="kpi-value tabular ${data.realized_pnl_total > 0 ? 'positive' : data.realized_pnl_total < 0 ? 'negative' : ''}" style="font-size:16px;">
              ${fmt.money(data.realized_pnl_total, 0)}
            </div>
            <div class="kpi-currency muted">${escapeHtml(data.realized_currency || "")}</div>
          </div>
        </div>

        ${evo.length >= 2 ? `
          <section>
            <h2>📈 Evolución del precio</h2>
            <div class="card">${sparklinePrice(evo)}</div>
          </section>
        ` : `
          <div class="card muted" style="margin-bottom:12px;">
            Sin suficientes precios históricos para graficar. Cargá precios con
            los loaders para ver la curva.
          </div>
        `}

        <section>
          <h2>📋 Operaciones (${movs.length})</h2>
          <div class="card">
            ${movs.length === 0 ? '<div class="muted">Sin operaciones</div>' :
              movs.map(m => {
                const isBuy = (m.qty || 0) > 0;
                const sign = isBuy ? "+" : "";
                const cls = isBuy ? "positive" : "negative";
                return `
                  <div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border);">
                    <div>
                      <div style="font-size:13px;">
                        <span class="tag ${isBuy ? '' : 'danger'}" style="font-size:10px;">
                          ${isBuy ? "BUY" : "SELL"}
                        </span>
                        ${escapeHtml(m.fecha)}
                      </div>
                      <div class="muted" style="font-size:11px;">${escapeHtml(m.account)}</div>
                    </div>
                    <div class="right">
                      <div class="tabular ${cls}">${sign}${fmt.money(m.qty, 4)}</div>
                      ${m.unit_price ? `<div class="sub muted tabular">@ ${fmt.money(m.unit_price, 4)} ${escapeHtml(m.currency || ccy)}</div>` : ""}
                    </div>
                  </div>
                `;
              }).join("")
            }
          </div>
        </section>
      </main>
    `;
  });

  // /asset-performance — tabla de retorno-desde-compra por holding actual
  route("/asset-performance", async () => {
    const view = sessionStorage.getItem("perf_view") || "all";
    const investible = view === "investible";
    let data, cashData;
    try {
      [data, cashData] = await Promise.all([
        API.assetPerformance(API.anchor(), investible),
        API.cashPerformance(API.anchor()),
      ]);
    } catch (e) {
      return `${headerWithBack("Performance por activo", "/")}
        <main><div class="card danger">${escapeHtml(e.message)}</div></main>`;
    }
    const items = data.items || [];
    const cashItems = (cashData && cashData.items || [])
      .filter(c => c.currency !== data.anchor);  // ocultar anchor (ret=0)

    return `
      ${headerWithBack("📊 Performance por activo", "/")}
      <main>
        <div class="toggle-pill" style="margin-bottom: 14px; width: 100%;">
          <button onclick="sessionStorage.setItem('perf_view','all'); render();" class="${view === 'all' ? 'active' : ''}" style="flex:1;">📦 Todo</button>
          <button onclick="sessionStorage.setItem('perf_view','investible'); render();" class="${view === 'investible' ? 'active' : ''}" style="flex:1;">💎 Invertible</button>
        </div>

        <div class="card compact muted" style="font-size:12px; margin-bottom: 8px;">
          Cómo viene rindiendo cada activo desde que lo incorporaste. Return %
          es (precio actual − avg cost) / avg cost en moneda nativa.
          Anualizado = (1 + return)^(365/días) − 1.
        </div>

        ${cashItems.length > 0 ? `
          <section style="margin-bottom: 16px;">
            <h2>💵 Cash vs ${escapeHtml(data.anchor)}</h2>
            <div class="card compact muted" style="font-size:11px; margin-bottom:6px;">
              Retorno del cash por evolución del FX desde que entró.
              <b>avg fx</b> = rate ponderado por entradas. Cash en ${escapeHtml(data.anchor)} no aparece (siempre 0%).
            </div>
            <div class="list">
              ${cashItems.map(c => {
                const ret = c.return_pct;
                const cls = ret == null ? "muted" : ret > 0 ? "positive" : "negative";
                const sign = ret > 0 ? "+" : "";
                return `
                  <div class="list-item" style="align-items:flex-start;">
                    <div class="meta">
                      <div class="meta-line1">
                        <span style="font-weight:700;">${escapeHtml(c.currency)}</span>
                        <span class="muted" style="font-size:11px;">cash · ${escapeHtml(c.account)}</span>
                      </div>
                      <div class="meta-line2 tabular muted" style="font-size:11px;">
                        ${fmt.money(c.qty, 2)} ${escapeHtml(c.currency)}
                        · avg fx ${fmt.money(c.avg_fx_in, 4)} → hoy ${fmt.money(c.current_fx, 4)}
                      </div>
                      <div class="meta-line2 muted" style="font-size:11px;">
                        desde ${escapeHtml(c.first_inflow_date || "?")} (${c.n_inflows} entradas)
                      </div>
                    </div>
                    <div class="right">
                      <div class="amount tabular ${cls}">
                        ${ret != null ? sign + (ret * 100).toFixed(2) + "%" : "—"}
                      </div>
                      <div class="sub tabular ${(c.pnl_anchor || 0) > 0 ? 'positive' : (c.pnl_anchor || 0) < 0 ? 'negative' : 'muted'}">
                        ${fmt.money(c.pnl_anchor, 0)} ${escapeHtml(data.anchor)}
                      </div>
                      <div class="sub muted tabular" style="font-size:11px;">
                        MV ${fmt.money(c.mv_anchor, 0)} ${escapeHtml(data.anchor)}
                      </div>
                    </div>
                  </div>
                `;
              }).join("")}
            </div>
          </section>
        ` : ""}

        <h2>🎯 Activos</h2>
        ${items.length === 0 ? `<div class="card muted">Sin holdings con retorno calculable.</div>` :
          `<div class="list">
            ${items.map((r, idx) => {
              const ret = r.return_pct;
              const retA = r.return_annualized;
              const cls = ret == null ? "muted" : ret > 0 ? "positive" : "negative";
              const sign = ret > 0 ? "+" : "";
              return `
                <a href="#/asset/${encodeURIComponent(r.asset)}?account=${encodeURIComponent(r.account)}" class="list-item" style="text-decoration:none; color:inherit; align-items:flex-start;">
                  <div class="meta">
                    <div class="meta-line1">
                      <span style="font-weight:700;">#${idx + 1}</span>
                      ${tickerHtml(r.asset, r.asset_class, r.name)}
                      <span class="muted" style="font-size:11px;">›</span>
                    </div>
                    <div class="meta-line2 muted" style="font-size:12px;">
                      ${escapeHtml(r.account)} · ${escapeHtml(assetClassLabel(r.asset_class))}
                    </div>
                    <div class="meta-line2 tabular muted" style="font-size:11px; margin-top:2px;">
                      desde ${escapeHtml(r.first_purchase_date || "?")}
                      ${r.days_held != null ? "(" + r.days_held + " días)" : ""}
                    </div>
                  </div>
                  <div class="right">
                    <div class="amount tabular ${cls}">
                      ${ret != null ? sign + (ret * 100).toFixed(2) + "%" : "—"}
                    </div>
                    <div class="sub muted tabular">
                      ${retA != null ? "ann " + (retA > 0 ? "+" : "") + (retA * 100).toFixed(1) + "%" : ""}
                    </div>
                    <div class="sub tabular ${(r.unrealized_pnl_native || 0) > 0 ? 'positive' : (r.unrealized_pnl_native || 0) < 0 ? 'negative' : 'muted'}" style="margin-top:2px;">
                      ${fmt.money(r.unrealized_pnl_native, 0)} ${escapeHtml(r.native_currency || "")}
                    </div>
                  </div>
                </a>
              `;
            }).join("")}
          </div>`
        }
      </main>
      ${bottomNav("/")}
    `;
  });

  window.toggleHoldFilter = function (key, btn) {
    const cur = sessionStorage.getItem(key);
    if (key === "hold_cash") {
      // Default: SHOW. Toggle hides (set "0"); restore shows (set "1").
      sessionStorage.setItem(key, cur === "0" ? "1" : "0");
    } else {
      // Default: HIDE. Toggle shows (set "1"); restore hides.
      sessionStorage.setItem(key, cur === "1" ? "0" : "1");
    }
    render();
  };

  // Search debounced
  document.addEventListener("input", (e) => {
    if (e.target && e.target.id === "holdSearch") {
      sessionStorage.setItem("hold_search", e.target.value);
      clearTimeout(window._searchTimer);
      window._searchTimer = setTimeout(() => render(), 300);
    }
  });
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
    const [data, meta] = await Promise.all([
      API.cash(),
      loadMeta().catch(() => ({ accountsRich: {} })),
    ]);
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
              <div class="list-item" style="align-items: center;">
                <div class="meta" style="flex: 1; min-width: 0;">
                  ${accountLabel(h.account, meta.accountsRich)}
                  <div class="meta-line2 muted" style="font-size: 11px; margin-top: 2px;">
                    ${escapeHtml((h.account_kind || "").toLowerCase())}${h.cash_purpose ? " · " + escapeHtml(h.cash_purpose) : ""}
                    ${!h.investible ? ' <span class="tag warn" style="font-size: 9px;">no-inv</span>' : ""}
                  </div>
                </div>
                <span class="tag" style="font-size: 10px; align-self: center;">${escapeHtml(h.currency)}</span>
                <div class="right" style="white-space: nowrap;">
                  <div class="amount tabular">${fmt.money(h.qty, 2)}</div>
                  <div class="sub muted">${fmt.money(h.mv_anchor)} ${escapeHtml(data.anchor)}</div>
                </div>
              </div>
            `).join("")}</div>`
          }
        </section>
      </main>
      ${bottomNav("/cash")}
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
  // /admin — gestión de users (solo admin)
  // ====================================================================
  route("/admin", async () => {
    let cfg, list;
    try {
      [cfg, list] = await Promise.all([API.config(), API.listUsers()]);
    } catch (e) {
      return `${headerWithBack("⚠️ Admin", "/")}<main><div class="card danger">${escapeHtml(e.message)}</div></main>`;
    }
    if (!cfg.is_admin) {
      return `
        ${headerWithBack("Admin", "/")}
        <main>
          <div class="card">
            <p>Esta página es solo para el admin.</p>
          </div>
        </main>
      `;
    }
    const users = list.users || [];
    const orphans = list.orphan_folders || [];
    return `
      ${headerWithBack("👥 Admin · Usuarios", "/")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Cada user tiene su Excel master, su DB y sus backups separados.
          Como admin podés <b>switch user</b> (read-only) para ver datos de otros,
          o crear/borrar usuarios.
        </div>

        ${!list.persistent ? `
          <div class="card warn" style="background:#FFF8E1; border-left: 4px solid var(--yellow); margin-top:12px; font-size: 13px;">
            <b>⚠ WM_USERS_FILE no configurado</b>
            <div style="margin-top: 4px;">
              Los users que crees ahora <b>se pierden al próximo reload</b> del web app.
              Para que se persistan automáticamente, agregá al WSGI file:
              <pre style="background:#fff; padding:6px; margin-top: 6px; font-size: 11px; overflow-x: auto;">os.environ['WM_USERS_FILE'] = '/home/rodricor/wealth_management_rodricor/users.json'</pre>
              Después reload del web app. La primera request crea el archivo
              con los users actuales y a partir de ahí todo se persiste solo.
            </div>
          </div>
        ` : ""}

        ${cfg.is_switched ? `
          <div class="card warn" style="background:#FFF8E1; border-left: 4px solid var(--yellow); margin-top:12px;">
            <b>⚠ Modo Switch User</b>
            <div style="font-size: 13px; margin-top: 4px;">
              Estás viendo los datos de <b>${escapeHtml(cfg.user_id)}</b> (read-only).
              POST/PUT/DELETE están bloqueados.
            </div>
            <button class="btn primary full" style="margin-top: 8px;"
                    data-onclick="switchToUser" data-arg="">
              ← Volver a mis datos (${escapeHtml(cfg.auth_user_id)})
            </button>
          </div>
        ` : ""}

        <section style="margin-top: 14px;">
          <h2>Usuarios (${users.length})</h2>
          <div class="list">
            ${users.map(u => `
              <div class="list-item" style="align-items: flex-start;">
                <div class="meta">
                  <div class="meta-line1">
                    ${escapeHtml(u.display_name)}
                    ${u.is_admin ? '<span class="tag">admin</span>' : ''}
                    ${u.user_id === cfg.auth_user_id ? '<span class="tag">vos</span>' : ''}
                  </div>
                  <div class="meta-line2">
                    <span class="muted">id:</span> ${escapeHtml(u.user_id)} ·
                    <span class="muted">tok:</span> <code style="font-size:10px;">${escapeHtml(u.token_preview)}</code>
                  </div>
                  <div class="meta-line2 muted" style="font-size: 11px;">
                    xlsx: ${u.has_xlsx ? '✓' : '✗'} · db: ${u.has_db ? '✓' : '✗'}
                  </div>
                </div>
                <div class="right" style="display: flex; flex-direction: column; gap: 4px;">
                  ${u.user_id !== cfg.auth_user_id ? `
                    <button class="btn ghost" style="padding: 4px 10px; font-size: 11px;"
                            data-onclick="switchToUser" data-arg="${escapeHtml(u.user_id)}">
                      👁 ver
                    </button>
                    <button class="btn ghost" style="padding: 4px 10px; font-size: 11px;"
                            data-onclick="seedDemoData" data-arg="${escapeHtml(u.user_id)}"
                            title="Sobreescribir master con datos demo fijos">
                      🎬 demo
                    </button>
                    <button class="btn" style="padding: 4px 10px; font-size: 11px; background: var(--red); color: white;"
                            data-onclick="deleteUserAction" data-arg="${escapeHtml(u.user_id)}">
                      🗑
                    </button>
                  ` : `<span class="muted" style="font-size:11px;">tú</span>`}
                </div>
              </div>
            `).join("")}
          </div>
        </section>

        <section style="margin-top: 14px;">
          <a href="#/admin/new" class="btn primary full">+ Crear usuario</a>
        </section>

        ${orphans.length > 0 ? `
          <section style="margin-top: 14px;">
            <h2>Folders huérfanos (${orphans.length})</h2>
            <div class="card compact muted" style="font-size: 12px;">
              Estos folders existen en disk pero no en config (probablemente
              users borrados con --delete_data=false). Editá WM_USERS_JSON
              para re-agregarlos o borralos manualmente.
            </div>
            <div class="list">
              ${orphans.map(o => `<div class="list-item">${escapeHtml(o)}</div>`).join("")}
            </div>
          </section>
        ` : ""}

        <section style="margin-top: 14px;">
          <a href="#/help" class="btn ghost full">❓ ¿Cómo persistir users a través de reloads?</a>
        </section>
      </main>
    `;
  });

  route("/admin/new", async () => {
    return `
      ${headerWithBack("Crear usuario", "/admin")}
      <main>
        <div class="card compact muted" style="font-size: 13px;">
          Se creará el folder <code>inputs/{user_id}/</code> y un Excel master
          completo (16 hojas) listo para que el amigo lo descargue, complete con
          sus saldos iniciales (en la hoja <code>_carga_inicial</code>) y suba
          de vuelta.
        </div>

        <form data-action="createUserSubmit" style="margin-top: 12px;">
          ${inputField("user_id (handle)", "user_id", "", "text",
                        { required: true, placeholder: "ej: amigo, marcos, juan_p" })}
          ${inputField("Nombre display", "display_name", "", "text",
                        { placeholder: "ej: Marcos Pérez (opcional)" })}
          <div class="field">
            <label>¿Es admin?</label>
            <select name="is_admin">
              <option value="0">No (default)</option>
              <option value="1">Sí (puede crear users + switch)</option>
            </select>
          </div>
          <button type="submit" class="btn primary full">Crear usuario</button>
        </form>

        <div class="card compact" style="background:#FFF8E1; margin-top: 14px; font-size: 12px;">
          ⚠ Después de crear el user, vas a recibir el token UNA SOLA VEZ.
          Compartilo con el amigo por un canal seguro (WhatsApp directo, no
          grupo). Y copiá el snippet del WSGI file que te muestra el alert.
        </div>
      </main>
    `;
  });

  // ====================================================================
  // /setup — wizard primera vez (user con DB vacía)
  // ====================================================================
  route("/setup", async () => {
    return `
      ${headerWithBack("🎉 Bienvenido", "/")}
      <main>
        <div class="card" style="border-left: 4px solid var(--green);">
          <h2 style="margin-bottom: 8px;">Setup inicial — 3 pasos</h2>
          <p>Para arrancar, necesitás cargar tus saldos iniciales (cuántos
          activos tenés hoy en cada cuenta). Tenés 2 caminos:</p>

          <h3 style="margin-top: 14px; font-size: 14px;">Opción A — Via Excel (recomendado para muchos saldos)</h3>
          <ol style="margin-left: 20px; font-size: 13px;">
            <li><b>Bajá tu Excel master</b> — abajo hay botón. Tiene 16 hojas
                ya preparadas (cuentas, especies, blotter, etc).</li>
            <li><b>Completalo en tu PC</b> — al menos las hojas:
                <ul style="margin-top: 4px;">
                  <li><code>cuentas</code>: tus cuentas (cocos, galicia, etc)</li>
                  <li><code>especies</code>: cada ticker que tradees</li>
                  <li><code>_carga_inicial</code>: tus holdings al día de hoy
                      (Cuenta | Activo | Qty | Precio | Moneda)</li>
                </ul>
            </li>
            <li><b>Subilo de vuelta</b> — botón abajo. La app procesa
                <code>_carga_inicial</code> automáticamente y te bootstrappea
                el portfolio.</li>
          </ol>

          <h3 style="margin-top: 14px; font-size: 14px;">Opción B — Cargar a mano desde la PWA</h3>
          <p>Andá a <b>Settings → Maestros → Cuentas</b> y agregá cuentas y
          especies a mano. Después usás <b>+ Nuevo trade / gasto / ingreso</b>
          en cada tab. Más lento pero no requiere PC.</p>
        </div>

        <section style="margin-top: 14px;">
          <a class="btn primary full" href="#" onclick="event.preventDefault(); downloadExcel();" style="margin-bottom: 8px;">
            ⬇ Bajar Excel master (template)
          </a>
          <button class="btn ghost full" onclick="document.getElementById('xlsxUpload').click();">
            ⬆ Subir Excel completado
          </button>
          <input type="file" id="xlsxUpload" accept=".xlsx,.xls"
                 style="display: none;" onchange="window.uploadInitialExcel(event);">
        </section>

        <div class="card compact muted" style="margin-top: 14px; font-size: 12px;">
          💡 Si más adelante querés agregar trades históricos (no solo el
          saldo actual), usá el tab <b>Trades</b> después del setup.
        </div>
      </main>
    `;
  });

  window.uploadInitialExcel = async function(event) {
    const file = event.target.files[0];
    if (!file) return;
    toast("Subiendo " + file.name + "...", "info");
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch(`${API.base}/api/upload/excel`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${API.token()}` },
        body: formData,
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { msg = (await res.json()).message || msg; } catch (_) {}
        throw new Error(msg);
      }
      const data = await res.json();
      const stats = data.import_stats || {};
      const summary = Object.entries(stats).filter(([_, v]) => v > 0)
                              .map(([k, v]) => `${k}: ${v}`).join(", ");
      toast(`✓ Excel subido. ${summary}`, "success");
      API._bustCache();
      invalidateMeta();
      // Si hay _carga_inicial, ofrecer procesarlo
      if (confirm("Excel subido. ¿Procesar la hoja _carga_inicial ahora? (genera asientos de apertura con tus saldos iniciales)")) {
        // Llamar refresh que re-importa
        await API.refresh();
        toast("Refresh completado. ¡Vamos al dashboard!", "success");
        navigate("/");
      } else {
        navigate("/");
      }
    } catch (e) {
      toast("Error: " + e.message, "error");
    }
  };

  // ====================================================================
  // /help — manualcitos
  // ====================================================================
  // ====================================================================
  // /performance — TWR + MWR + métricas completas
  // ====================================================================
  route("/performance", async () => {
    const view = sessionStorage.getItem("perf_view") || "all";  // 'all' | 'investible'
    const investible = view === "investible";
    let perf, ec, tstats, rpnl, holdingsResp;
    try {
      [perf, ec, tstats, rpnl, holdingsResp] = await Promise.all([
        API.performance(API.anchor(), investible),
        API.equityCurve(API.anchor(), investible),
        API.tradeStats(),
        API.realizedPnl(),
        API.holdings(API.anchor()),
      ]);
    } catch (e) {
      return `${headerWithBack("📊 Performance", "/settings")}
        <main><div class="card danger">${escapeHtml(e.message)}</div></main>
        ${bottomNav("/settings")}`;
    }
    const twr = perf.twr || {};
    const mwr = perf.mwr || {};

    const fmtPct = (p) => {
      if (p == null) return '<span class="muted">·</span>';
      const cls = p > 0 ? "positive" : p < 0 ? "negative" : "muted";
      const sign = p > 0 ? "+" : "";
      return `<span class="${cls}">${sign}${(p * 100).toFixed(2)}%</span>`;
    };

    // Sparkline SVG de la curva de PN
    const curve = (ec && ec.total) || [];
    function sparkline(points) {
      if (!points || points.length < 2) return "";
      const w = 320, h = 90, pad = 4;
      const vals = points.map(p => p.mv_anchor);
      const min = Math.min(...vals), max = Math.max(...vals);
      const range = (max - min) || 1;
      const stepX = (w - pad * 2) / (points.length - 1);
      const coords = points.map((p, i) => {
        const x = pad + i * stepX;
        const y = h - pad - ((p.mv_anchor - min) / range) * (h - pad * 2);
        return [x, y];
      });
      const path = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
      const last = coords[coords.length - 1];
      return `
        <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%; height:90px; display:block;">
          <path d="${path}" fill="none" stroke="#1F3864" stroke-width="2"/>
          <circle cx="${last[0]}" cy="${last[1]}" r="3" fill="#1F3864"/>
        </svg>
        <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--muted); margin-top:4px;">
          <span>${escapeHtml(points[0].fecha)}</span>
          <span>${escapeHtml(points[points.length - 1].fecha)}</span>
        </div>
      `;
    }

    // Trade stats por moneda — orden ARS, USB, USD, otros
    const byCcy = (tstats && tstats.by_currency) || {};
    const CCY_ORDER = ["ARS", "USB", "USD"];
    const ccySort = (a, b) => {
      const ia = CCY_ORDER.indexOf(a), ib = CCY_ORDER.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    };
    const sortedCcys = Object.keys(byCcy).sort(ccySort);
    const totalsByCcy = (rpnl && rpnl.totals_by_currency) || {};

    // Unrealized PnL por moneda nativa (a partir de holdings)
    const unrealByCcy = {};
    for (const h of (holdingsResp && holdingsResp.items) || []) {
      const c = h.native_currency || "?";
      const v = h.unrealized_pnl_native;
      if (v == null || isNaN(v)) continue;
      if (!unrealByCcy[c]) unrealByCcy[c] = { pnl: 0, n: 0 };
      unrealByCcy[c].pnl += v;
      unrealByCcy[c].n += 1;
    }
    const sortedUnrealCcys = Object.keys(unrealByCcy).sort(ccySort);

    return `
      ${headerWithBack("📊 Performance", "/settings")}
      <main class="has-bottom-nav">
        <div class="toggle-pill" style="margin-bottom: 14px; width: 100%;">
          <button onclick="sessionStorage.setItem('perf_view','all'); render();" class="${view === 'all' ? 'active' : ''}" style="flex:1;">📦 Todo</button>
          <button onclick="sessionStorage.setItem('perf_view','investible'); render();" class="${view === 'investible' ? 'active' : ''}" style="flex:1;">💎 Invertible</button>
        </div>

        ${perf.curve_points < 2 ? `
          <div class="card warn" style="background:#FFF8E1; border-left:4px solid var(--yellow);">
            <b>⚠ Datos insuficientes</b>
            <div class="muted" style="font-size:12px; margin-top:4px;">
              Necesito ≥2 snapshots de PN para calcular returns.
              Tenés ${perf.curve_points || 0}. Cada <code>refresh</code> graba un snapshot;
              esperá unos días o forzá refrescos para tener historia.
            </div>
          </div>
        ` : ""}

        ${curve.length >= 2 ? `
          <section>
            <h2>📈 Equity curve</h2>
            <div class="card">
              ${sparkline(curve)}
              <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:13px;">
                <div><span class="muted">PN inicial:</span> <b class="tabular">${fmt.money(curve[0].mv_anchor)}</b></div>
                <div><span class="muted">PN actual:</span> <b class="tabular">${fmt.money(curve[curve.length - 1].mv_anchor)}</b></div>
              </div>
              <div class="muted" style="font-size:11px; margin-top:6px;">${curve.length} snapshots · ${escapeHtml(perf.anchor_currency || "")}</div>
            </div>
          </section>
        ` : ""}

        <section>
          <h2>📊 PnL realizado por moneda</h2>
          <div class="card compact muted" style="font-size:12px; margin-bottom: 8px;">
            Trades cerrados (FIFO). Los totales no se suman entre monedas porque ARS/USB/USD no son intercambiables sin FX.
          </div>
          ${sortedCcys.length === 0 ? `<div class="card muted">Sin trades cerrados aún</div>` :
            `<div class="card">
              ${sortedCcys.map(ccy => {
                const s = byCcy[ccy];
                const tot = totalsByCcy[ccy];
                const cls = s.net_pnl > 0 ? "positive" : s.net_pnl < 0 ? "negative" : "muted";
                return `
                  <div style="padding:10px 0; border-bottom:1px solid var(--border);">
                    <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px;">
                      <strong>${escapeHtml(ccy)}</strong>
                      <span class="tabular ${cls}">${fmt.money(s.net_pnl)} ${escapeHtml(ccy)}</span>
                    </div>
                    <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px 8px; font-size:12px;">
                      <div><span class="muted">Trades:</span> <b>${s.n_trades}</b></div>
                      <div><span class="muted">Winrate:</span> <b>${(s.winrate * 100).toFixed(1)}%</b></div>
                      <div><span class="muted">Wins / Loss:</span> <b class="positive">${s.n_winners}</b> / <b class="negative">${s.n_losers}</b></div>
                      <div><span class="muted">Profit factor:</span> <b>${s.profit_factor != null ? s.profit_factor.toFixed(2) : "∞"}</b></div>
                      <div><span class="muted">Avg winner:</span> <b class="positive">${fmt.money(s.avg_winner)}</b></div>
                      <div><span class="muted">Avg loser:</span> <b class="negative">${fmt.money(s.avg_loser)}</b></div>
                      <div><span class="muted">Best:</span> <b class="positive">${fmt.money(s.best_trade)}</b></div>
                      <div><span class="muted">Worst:</span> <b class="negative">${fmt.money(s.worst_trade)}</b></div>
                      <div><span class="muted">Expectancy:</span> <b>${fmt.money(s.expectancy)}</b></div>
                      <div><span class="muted">Hold avg:</span> <b>${(s.avg_holding_days || 0).toFixed(1)}d</b></div>
                      <div><span class="muted">Streak W / L:</span> <b>${s.largest_streak_wins} / ${s.largest_streak_losses}</b></div>
                      <div><span class="muted">Scratch:</span> <b>${s.n_scratch}</b></div>
                    </div>
                  </div>
                `;
              }).join("")}
            </div>`
          }
        </section>

        <section>
          <h2>📈 PnL NO realizado (mark-to-market)</h2>
          <div class="card compact muted" style="font-size:12px; margin-bottom: 8px;">
            Posiciones abiertas — diferencia entre precio actual y costo promedio, en moneda nativa de cada activo.
          </div>
          ${sortedUnrealCcys.length === 0 ? `<div class="card muted">Sin posiciones abiertas</div>` :
            `<div class="card">
              ${sortedUnrealCcys.map(ccy => {
                const u = unrealByCcy[ccy];
                const cls = u.pnl > 0 ? "positive" : u.pnl < 0 ? "negative" : "muted";
                return `
                  <div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border);">
                    <div>
                      <strong>${escapeHtml(ccy)}</strong>
                      <span class="muted" style="font-size:12px;"> · ${u.n} posiciones</span>
                    </div>
                    <span class="tabular ${cls}">${fmt.money(u.pnl)} ${escapeHtml(ccy)}</span>
                  </div>
                `;
              }).join("")}
            </div>`
          }
        </section>

        <section>
          <h2>🧹 Mantenimiento de snapshots</h2>
          <div class="card compact muted" style="font-size:12px; margin-bottom: 8px;">
            <b>Reconstruir histórico</b>: calcula el PN en cada fecha pasada
            usando tu historial de movimientos — la curva arranca desde el
            primer evento, no desde hoy. <br>
            <b>Resetear</b>: borra todos los snapshots y arranca desde cero.
          </div>
          <button class="btn primary full" data-onclick="backfillSnapshots" style="margin-bottom:6px;">
            🔄 Reconstruir histórico desde movimientos
          </button>
          <button class="btn ghost full" data-onclick="resetSnapshots">🗑 Resetear snapshots</button>
        </section>

        <section>
          <h2>Time Weighted Return (TWR)</h2>
          <div class="card compact muted" style="font-size:12px; margin-bottom: 8px;">
            Aísla flujos: muestra cuánto rinde lo que tenés invertido,
            independiente de cuándo metiste/sacaste plata.
            <b>Es la métrica estándar de la industria</b> para comparar
            portfolios o estrategias.
          </div>
          <div class="kpi-grid">
            <div class="kpi">
              <div class="kpi-label">Return total</div>
              <div class="kpi-value tabular">${fmtPct(twr.twr_pct)}</div>
              <div class="kpi-currency muted">${perf.from_date || "?"} → ${perf.to_date || "?"}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Anualizado</div>
              <div class="kpi-value tabular">${fmtPct(twr.twr_annual)}</div>
              <div class="kpi-currency muted">${twr.n_periods || 0} sub-periodos</div>
            </div>
          </div>
        </section>

        <section>
          <h2>Money Weighted Return (MWR)</h2>
          <div class="card compact muted" style="font-size:12px; margin-bottom: 8px;">
            Ponderado por timing — refleja el éxito <b>real</b> tuyo.
            Si metiste plata justo antes de un rally, el MWR sube; si metiste
            antes de una caída, baja. Calculado como <i>Modified Dietz</i>
            (proxy ≈ IRR).
          </div>
          <div class="kpi-grid">
            <div class="kpi">
              <div class="kpi-label">Return total</div>
              <div class="kpi-value tabular">${fmtPct(mwr.mwr_pct)}</div>
              <div class="kpi-currency muted">flujos = ${fmt.money(mwr.total_flow || 0, 0)} ${escapeHtml(perf.anchor_currency)}</div>
            </div>
            <div class="kpi">
              <div class="kpi-label">Anualizado</div>
              <div class="kpi-value tabular">${fmtPct(mwr.mwr_annual)}</div>
              <div class="kpi-currency muted">${mwr.n_flows || 0} flujos</div>
            </div>
          </div>
        </section>

        <section>
          <h2>Resumen del período</h2>
          <div class="card">
            <div style="display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--border);">
              <span class="muted">PN inicial</span>
              <span class="tabular">${fmt.money(perf.v_begin || 0)} ${escapeHtml(perf.anchor_currency)}</span>
            </div>
            <div style="display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--border);">
              <span class="muted">PN final</span>
              <span class="tabular">${fmt.money(perf.v_end || 0)} ${escapeHtml(perf.anchor_currency)}</span>
            </div>
            <div style="display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--border);">
              <span class="muted">Cambio bruto</span>
              <span class="tabular ${(perf.total_change_abs||0)>=0?'positive':'negative'}">${fmt.money(perf.total_change_abs || 0)} ${escapeHtml(perf.anchor_currency)}</span>
            </div>
            <div style="display:flex; justify-content:space-between; padding:6px 0;">
              <span class="muted">Flujos netos (aportes - retiros)</span>
              <span class="tabular">${fmt.money(perf.total_flows || 0)} ${escapeHtml(perf.anchor_currency)}</span>
            </div>
          </div>
        </section>

        ${(perf.flows || []).length > 0 ? `
          <section>
            <h2>Flujos detectados (${perf.flows.length})</h2>
            <div class="card compact muted" style="font-size:12px; margin-bottom: 8px;">
              Sueldos, gastos, opening_balance. Positivo = entró capital.
            </div>
            <div class="list" style="max-height: 240px; overflow-y: auto;">
              ${perf.flows.slice(-30).reverse().map(f => `
                <div class="list-item">
                  <div class="meta">
                    <div class="meta-line1">${escapeHtml(fmt.date(f.fecha))}</div>
                  </div>
                  <div class="right">
                    <div class="amount tabular ${f.amount_anchor >= 0 ? 'positive' : 'negative'}">
                      ${f.amount_anchor >= 0 ? '+' : ''}${fmt.money(f.amount_anchor)}
                    </div>
                    <div class="sub muted">${escapeHtml(perf.anchor_currency)}</div>
                  </div>
                </div>
              `).join("")}
            </div>
          </section>
        ` : ""}
      </main>
      ${bottomNav("/settings")}
    `;
  });

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
    let cfg = null;
    let appSettings = null;
    try { health = await API.health(); } catch (_) {}
    try { cfg = await API.config(); } catch (_) {}
    try { appSettings = await API.getSettings(); } catch (_) {}
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
              <span class="tag ${cfg?.xlsx_present ? "" : "warn"}">${cfg?.xlsx_present ? "sí" : "no"}</span>
            </div>
            <div style="display:flex; justify-content:space-between; padding: 4px 0;">
              <span class="muted">DB lista</span>
              <span class="tag ${cfg?.db_present ? "" : "warn"}">${cfg?.db_present ? "sí" : "no"}</span>
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
          <h2>Tenencias y cotizaciones</h2>
          <a href="#/holdings" class="btn ghost full" style="margin-bottom:6px">📋 Holdings (todas las posiciones)</a>
          <a href="#/cotizaciones" class="btn ghost full" style="margin-bottom:6px">💹 Cotizaciones (precios + FX)</a>
          <a href="#/cash" class="btn ghost full" style="margin-bottom:6px">💵 Cash por cuenta</a>
          <a href="#/pasivos" class="btn ghost full" style="margin-bottom:6px">📉 Pasivos</a>
          <a href="#/funding" class="btn ghost full" style="margin-bottom:6px">💰 Funding (cauciones)</a>
          <a href="#/transferencias" class="btn ghost full" style="margin-bottom:6px">🔄 Transferencias</a>
        </section>

        <section>
          <h2>Trading apalancado</h2>
          <a href="#/leverage" class="btn ghost full" style="margin-bottom:6px">⚡ Leverage view (operaciones apalancadas)</a>
          <a href="#/calculator" class="btn ghost full" style="margin-bottom:6px">🧮 What-if calculator</a>
          <a href="#/journal" class="btn ghost full" style="margin-bottom:6px">📔 Trading journal (por strategy)</a>
          <a href="#/calendar" class="btn ghost full" style="margin-bottom:6px">📅 Calendario (próximos 60 días)</a>
        </section>

        <section>
          <h2>🎯 Alertas de target / stop</h2>
          <form data-action="setAlertDistance">
            <div class="card compact">
              <label style="font-size: 13px; display: block; margin-bottom: 6px;">
                Distancia para alertar (en bps, 1 bp = 0.01%):
              </label>
              <div style="display: flex; gap: 8px; align-items: center;">
                <input type="number" name="alert_distance_bps"
                       min="0" max="10000" step="1"
                       value="${appSettings?.alert_distance_bps ?? 10}"
                       style="flex: 1; padding: 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px;">
                <button type="submit" class="btn primary" style="padding: 8px 14px;">Guardar</button>
              </div>
              <div class="muted" style="font-size: 11px; margin-top: 6px;">
                10 bps = 0.10% (target casi tocado). 100 bps = 1%.
                Cambios persistidos en DB (no en Excel).
              </div>
            </div>
          </form>
        </section>

        <section>
          <h2>Cuenta</h2>
          <a href="#/me" class="btn ghost full" style="margin-bottom:6px">
            👤 Mi perfil + contraseña
          </a>
        </section>

        ${cfg && cfg.is_admin ? `
          <section>
            <h2>Admin</h2>
            ${cfg.is_admin ? `
              <a href="#/superadmin" class="btn primary full" style="margin-bottom:6px">⭐ Superadmin (auth users)</a>
            ` : ''}
            <a href="#/admin" class="btn ghost full" style="margin-bottom:6px">👥 Users legacy (token)</a>
            ${cfg.is_switched ? `
              <button class="btn ghost full" data-onclick="switchToUser" data-arg="" style="margin-bottom:6px">
                ← Volver a tu user (${escapeHtml(cfg.auth_user_id)})
              </button>
            ` : ""}
          </section>
        ` : ""}

        <section>
          <h2>🔐 Brokers y credenciales</h2>
          <a href="#/credentials" class="btn ghost full" style="margin-bottom:8px">
            🔑 Configurar credenciales (BYMA, Binance, IBKR${cfg && cfg.is_superadmin ? ", CAFCI" : ""})
          </a>
          <a href="#/import" class="btn primary full" style="margin-bottom:8px">
            📥 Auto-importar tenencias
          </a>
          <button class="btn ghost full" data-onclick="runByma" style="margin-bottom:8px">
            🔄 Refrescar precios BYMA
          </button>
          <button class="btn ghost full" data-onclick="runCripto" style="margin-bottom:8px">
            🪙 Refrescar precios cripto
          </button>
          ${cfg && cfg.is_superadmin ? `
            <button class="btn ghost full" data-onclick="runCafci" style="margin-bottom:8px">
              📊 Refrescar VCP de FCIs (CAFCI)
            </button>
          ` : ""}
        </section>

        <section>
          <h2>Acciones</h2>
          <button class="btn primary full" data-onclick="refreshAll" style="margin-bottom:8px">⟳ Refrescar DB desde Excel</button>
          <a class="btn ghost full" href="${API.base}/api/download/excel"
             style="margin-bottom:8px"
             onclick="event.preventDefault(); downloadExcel();">⬇ Descargar Excel master</a>
          <button class="btn ghost full" style="margin-bottom:8px"
                  onclick="document.getElementById('xlsxUploadSettings').click();">
            ⬆ Subir / reemplazar Excel master
          </button>
          <input type="file" id="xlsxUploadSettings" accept=".xlsx,.xls"
                 style="display:none" onchange="window.uploadInitialExcel(event);">
          <a class="btn ghost full" href="#/setup" style="margin-bottom:8px">🎉 Wizard inicial</a>
          <a class="btn ghost full" href="#/welcome" style="margin-bottom:8px">🚀 Onboarding paso a paso</a>
          <a class="btn ghost full" href="#/help" style="margin-bottom:8px">❓ Ayuda y manualcitos</a>
          <a class="btn ghost full" href="#/audit" style="margin-bottom:8px">📜 Audit log (últimas 100)</a>
          <button class="btn danger full" data-onclick="logout">🔓 Cerrar sesión</button>
        </section>

        <section>
          <h2>📜 Privacidad y datos</h2>
          <a class="btn ghost full" style="margin-bottom:8px"
             onclick="event.preventDefault(); downloadAccountExport();"
             href="${API.base}/api/account/export">⬇ Exportar todos mis datos (ZIP)</a>
          <a class="btn ghost full" href="#/privacy" style="margin-bottom:8px">📄 Política de privacidad</a>
          <a class="btn ghost full" href="#/terms" style="margin-bottom:8px">📄 Términos y condiciones</a>
          <button class="btn danger full" data-onclick="deleteMyAccount">🗑 Borrar mis datos</button>
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
      ${bottomNav("/settings")}
    `;
  });

  // ====================================================================
  // /me — Perfil + cambio de contraseña + sessions
  // ====================================================================
  route("/me", async () => {
    let me;
    try { me = await API.req("/api/auth/me", { noCache: true }); }
    catch (e) {
      return `${headerWithBack("👤 Mi cuenta", "/settings")}
        <main><div class="card">Error: ${escapeHtml(e.message)}</div></main>`;
    }
    const u = me.user || {};
    const isLegacy = me.auth_via === "legacy";
    const sessions = me.sessions || [];
    return `
      ${headerWithBack("👤 Mi cuenta", "/settings")}
      <main>
        <section>
          <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
              <div>
                <div style="font-size:18px; font-weight:700;">${escapeHtml(u.display_name || u.user_id || "")}</div>
                <div class="muted" style="font-size:13px;">${escapeHtml(u.email || "(sin email — modo legacy)")}</div>
                <div class="muted" style="font-size:11px; margin-top:4px;">user_id: <code>${escapeHtml(u.user_id || "")}</code></div>
              </div>
              <div style="text-align:right;">
                ${u.is_superadmin ? '<span class="tag" style="background: var(--orange-soft); color: var(--orange);">⭐ superadmin</span><br>' : ''}
                ${u.is_admin && !u.is_superadmin ? '<span class="tag">admin</span><br>' : ''}
                ${u.email_verified === true ? '<span class="tag success">✓ verificado</span>' :
                  u.email_verified === false ? '<span class="tag warn">sin verificar</span>' : ''}
              </div>
            </div>
          </div>
        </section>

        ${isLegacy ? `
          <div class="card compact" style="background: var(--orange-soft); color: var(--text);">
            ⚠ Estás logueado con un token legacy (sin email/password).
            Migrá a una cuenta real con
            <a href="#/signup" style="text-decoration:underline; color: var(--accent);">signup</a>
            para recuperación + 2FA + audit por email.
          </div>
        ` : `
          <section>
            <h2>Cambiar nombre</h2>
            <form data-action="updateDisplayName">
              <div style="display:flex; gap:8px;">
                <input name="display_name" type="text" value="${escapeHtml(u.display_name || '')}"
                       style="flex:1; padding:10px; border:1px solid var(--border); border-radius:8px;">
                <button type="submit" class="btn primary">Guardar</button>
              </div>
            </form>
          </section>

          <section>
            <h2>Cambiar contraseña</h2>
            <form data-action="changePassword">
              <div class="field">
                <label>Contraseña actual</label>
                <input type="password" name="current_password" required minlength="8"
                       autocomplete="current-password">
              </div>
              <div class="field">
                <label>Nueva contraseña</label>
                <input type="password" name="new_password" required minlength="8"
                       autocomplete="new-password">
              </div>
              <div class="field">
                <label>Confirmar</label>
                <input type="password" name="confirm" required minlength="8">
              </div>
              <button type="submit" class="btn primary full" style="margin-top:8px;">
                Cambiar contraseña
              </button>
              <div class="muted" style="font-size:12px; margin-top:6px;">
                Esto invalida todas las otras sesiones (vas a tener que
                volver a loguearte en cada device).
              </div>
            </form>
          </section>

          ${u.email_verified === false ? `
            <section>
              <button class="btn ghost full" data-onclick="resendVerify">
                ✉️ Re-enviar email de verificación
              </button>
            </section>
          ` : ''}

          <section>
            <h2>Sesiones activas (${sessions.length})</h2>
            ${sessions.length === 0 ? '<div class="card muted">Sin sesiones.</div>' :
              `<div class="list">${sessions.map(s => `
                <div class="list-item" style="align-items: flex-start;">
                  <div class="meta">
                    <div class="meta-line1 tabular" style="font-size:13px;">
                      ${escapeHtml(s.token_prefix)}…
                    </div>
                    <div class="meta-line2 muted" style="font-size:11px;">
                      Última: ${escapeHtml(s.last_used_at || '')}
                      · Expira: ${escapeHtml(s.expires_at || '')}<br>
                      ${escapeHtml((s.user_agent || '').slice(0, 60))}
                      ${s.ip ? '· ' + escapeHtml(s.ip) : ''}
                    </div>
                  </div>
                  <button class="btn ghost" data-onclick="revokeSession" data-arg="${escapeHtml(s.token_prefix)}"
                          style="padding:6px 10px; font-size:12px;">Revocar</button>
                </div>
              `).join('')}</div>`
            }
            <button class="btn danger full" data-onclick="revokeAllSessions" style="margin-top:8px;">
              Revocar todas las otras sesiones
            </button>
          </section>
        `}
      </main>
      ${bottomNav("/settings")}
    `;
  });

  // ====================================================================
  // /superadmin — Gestión de auth_users (solo superadmin)
  // ====================================================================
  route("/superadmin", async () => {
    let data;
    try { data = await API.req("/api/superadmin/users", { noCache: true }); }
    catch (e) {
      return `${headerWithBack("⭐ Superadmin", "/settings")}
        <main><div class="card">${escapeHtml(e.message)}</div></main>`;
    }
    const users = data.users || [];
    const me = data.self;
    return `
      ${headerWithBack("⭐ Superadmin", "/settings")}
      <main>
        <section>
          <div class="card compact muted" style="font-size:13px;">
            Sos el superadmin del deploy. Podés ver y gestionar todos los
            users registrados con email/password. Los users legacy (token
            estático) viven aparte en
            <a href="#/admin" style="color:var(--accent);">/admin</a>.
          </div>
        </section>
        <section>
          <h2>Users (${users.length})</h2>
          <div class="list">
            ${users.map(u => {
              const isMe = u.user_id === me;
              const flagAdmin = u.is_admin ? '<span class="tag">admin</span>' : '';
              const flagSuper = u.is_superadmin ? '<span class="tag" style="background:var(--orange-soft); color:var(--orange);">⭐ super</span>' : '';
              const flagVerified = u.email_verified
                ? '<span class="tag success" style="font-size:9px;">✓</span>'
                : '<span class="tag warn" style="font-size:9px;">sin verif</span>';
              return `
                <div class="list-item" style="align-items:flex-start;">
                  <div class="meta">
                    <div class="meta-line1">
                      ${escapeHtml(u.display_name || u.email)}
                      ${isMe ? ' <span class="tag">vos</span>' : ''}
                      ${flagSuper} ${flagAdmin} ${flagVerified}
                    </div>
                    <div class="meta-line2 muted" style="font-size:12px;">
                      ${escapeHtml(u.email)} · ${escapeHtml(u.user_id)}
                      <br>creado ${escapeHtml((u.created_at || '').slice(0, 10))}
                      ${u.last_login_at ? ' · último login ' + escapeHtml(u.last_login_at.slice(0, 10)) : ''}
                    </div>
                  </div>
                  ${!isMe ? `
                    <div style="display:flex; flex-direction:column; gap:4px;">
                      <button class="btn ghost" style="padding:4px 8px; font-size:11px;"
                              data-onclick="toggleAdmin" data-arg="${escapeHtml(u.user_id)}|${u.is_admin ? '0' : '1'}">
                        ${u.is_admin ? '⊖ admin' : '⊕ admin'}
                      </button>
                      <button class="btn ghost" style="padding:4px 8px; font-size:11px;"
                              data-onclick="toggleSuper" data-arg="${escapeHtml(u.user_id)}|${u.is_superadmin ? '0' : '1'}">
                        ${u.is_superadmin ? '⊖ super' : '⊕ super'}
                      </button>
                      <button class="btn danger" style="padding:4px 8px; font-size:11px;"
                              data-onclick="deleteAuthUser" data-arg="${escapeHtml(u.user_id)}">
                        🗑
                      </button>
                    </div>
                  ` : ''}
                </div>
              `;
            }).join('')}
          </div>
        </section>
      </main>
      ${bottomNav("/settings")}
    `;
  });

  // ====================================================================
  // /import — Auto-import de tenencias desde brokers
  // ====================================================================
  route("/import", async () => {
    let data;
    try { data = await API.req("/api/import/brokers", { noCache: true }); }
    catch (e) {
      return `${headerWithBack("📥 Auto-import", "/settings")}
        <main><div class="card">${escapeHtml(e.message)}</div></main>`;
    }
    const brokers = data.brokers || [];
    return `
      ${headerWithBack("📥 Auto-import", "/settings")}
      <main>
        <section>
          <div class="card compact muted" style="font-size:13px;">
            Conectá tu broker para importar tus tenencias automáticamente.
            Las credenciales se guardan encriptadas y son <b>read-only</b> —
            esta app NUNCA opera por vos. Si el broker pide permisos,
            elegí solo "Reading"/"View".
          </div>
        </section>
        <section>
          <h2>Brokers disponibles</h2>
          ${brokers.map(b => `
            <div class="card" style="margin-bottom:12px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <div>
                  <div style="font-weight:700; font-size:16px;">
                    ${b.icon} ${escapeHtml(b.name)}
                  </div>
                  <div class="muted" style="font-size:12px;">
                    ${b.ready
                      ? '<span class="tag success">credenciales listas</span>'
                      : '<span class="tag warn">faltan credenciales</span>'}
                  </div>
                </div>
                <div style="display:flex; gap:6px;">
                  ${b.ready ? `
                    <button class="btn ghost" data-onclick="testBroker"
                            data-arg="${escapeHtml(b.id)}"
                            style="padding:6px 10px; font-size:12px;">
                      Probar
                    </button>
                    <a class="btn primary" href="#/import/${escapeHtml(b.id)}"
                       style="padding:6px 14px; font-size:13px;">
                      Importar →
                    </a>
                  ` : `
                    <a class="btn primary" href="#/credentials"
                       style="padding:6px 14px; font-size:13px;">
                      Configurar
                    </a>
                  `}
                </div>
              </div>
              <div class="muted" style="font-size:12px; line-height:1.4;">
                ${escapeHtml(b.help)}
              </div>
            </div>
          `).join("")}
        </section>
      </main>
      ${bottomNav("/settings")}
    `;
  });

  // ====================================================================
  // /import/:broker — Preview + apply de un broker específico
  // ====================================================================
  route("/import/:broker", async ({ broker }) => {
    let preview, meta;
    try {
      [preview, meta] = await Promise.all([
        API.req(`/api/import/${broker}/preview`, { method: "POST", json: {} }),
        loadMeta(),
      ]);
    } catch (e) {
      return `${headerWithBack("📥 Importar", "/import")}
        <main>
          <div class="card">
            <div style="font-weight:700; margin-bottom:8px;">No pude bajar las posiciones.</div>
            <div class="muted" style="font-size:13px;">${escapeHtml(e.message)}</div>
            <a href="#/credentials" class="btn ghost full" style="margin-top:12px;">Revisar credenciales</a>
          </div>
        </main>`;
    }
    const positions = preview.positions || [];
    const asOf = preview.as_of || "";
    const accounts = meta.accounts || [];

    return `
      ${headerWithBack(`📥 Importar de ${escapeHtml(broker)}`, "/import")}
      <main>
        <section>
          <div class="card compact" style="margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between;">
              <div>
                <b>${positions.length}</b> posiciones · al <b>${escapeHtml(asOf)}</b>
              </div>
              <button class="btn ghost" onclick="document.querySelectorAll('input[name=imp]').forEach(c => c.checked = !c.checked)"
                      style="padding:4px 10px; font-size:11px;">Toggle todos</button>
            </div>
            ${(preview.warnings || []).length > 0 ? `
              <div class="muted" style="font-size:12px; margin-top:6px;">
                ⚠ ${preview.warnings.map(escapeHtml).join("; ")}
              </div>
            ` : ""}
          </div>
        </section>

        <form data-action="applyImport" id="import-form">
          <input type="hidden" name="broker" value="${escapeHtml(broker)}">

          <section>
            <h2>Configuración</h2>
            <div class="field">
              <label>Cuenta destino *</label>
              <select name="account" required>
                ${accounts.map(a =>
                  `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`
                ).join("")}
              </select>
            </div>
            <div class="field">
              <label>Fecha del saldo</label>
              <input type="date" name="fecha" value="${fmt.today()}">
            </div>
            <div class="field" style="display:flex; align-items:center; gap:8px;">
              <input type="checkbox" name="create_missing_assets" id="create-missing" checked
                     style="width:auto;">
              <label for="create-missing" style="margin:0;">
                Crear assets nuevos automáticamente en <code>especies</code>
              </label>
            </div>
          </section>

          <section>
            <h2>Posiciones a importar</h2>
            <div class="list">
              ${positions.length === 0 ? `<div class="card muted">Sin posiciones.</div>` :
                positions.map((p, i) => `
                <label class="list-item" style="cursor:pointer; align-items:flex-start;">
                  <input type="checkbox" name="imp" value="${i}" checked
                         style="width:auto; margin-right:8px; margin-top:4px;">
                  <div class="meta" style="flex:1;">
                    <div class="meta-line1">
                      ${tickerHtml(p.ticker, p.asset_class)}
                      ${p.is_cash ? '<span class="tag" style="font-size:9px;">cash</span>' : ''}
                      ${p.known === false ? '<span class="tag warn" style="font-size:9px;">nuevo</span>' :
                        p.known === true ? '<span class="tag success" style="font-size:9px;">existe</span>' : ''}
                    </div>
                    <div class="meta-line2 muted" style="font-size:11px;">
                      ${escapeHtml(p.name || '')} · ${escapeHtml(p.asset_class)}
                      ${p.avg_price != null ? '· avg ' + fmt.money(p.avg_price, 4) : ''}
                    </div>
                  </div>
                  <div class="right">
                    <div class="amount tabular">${fmt.money(p.qty, 6)}</div>
                    <div class="sub muted">${escapeHtml(p.currency)}</div>
                  </div>
                  <input type="hidden" name="data_${i}"
                         value='${escapeHtml(JSON.stringify(p))}'>
                </label>
              `).join("")}
            </div>
          </section>

          ${positions.length > 0 ? `
            <button type="submit" class="btn primary full" style="margin-top:14px;">
              Importar seleccionadas
            </button>
            <div class="muted" style="font-size:12px; text-align:center; margin-top:8px;">
              Se escribe al sheet <code>_carga_inicial</code> y se re-importa el master.
            </div>
          ` : ""}
        </form>
      </main>
    `;
  });

  // ====================================================================
  // /carga-inicial — Carga manual de tenencias viejas (saldos de apertura)
  // ====================================================================
  route("/carga-inicial", async () => {
    const meta = await loadMeta();
    const realAccounts = (meta.accounts || []).filter(c =>
      !c.startsWith("caucion_") &&
      !["external_income", "external_expense",
        "interest_income", "interest_expense",
        "opening_balance"].includes(c)
    );
    if (realAccounts.length === 0) {
      return `${headerWithBack("📦 Cargar tenencias viejas", "/welcome")}
        <main>
          <section>
            <div class="card">
              <b>Primero creá al menos una cuenta.</b>
              <div class="muted" style="font-size:13px; margin:6px 0 12px;">
                Necesitás una cuenta destino (banco, broker, wallet) para
                asignar los saldos de apertura.
              </div>
              <a href="#/cuentas" class="btn primary full">Ir a cuentas</a>
            </div>
          </section>
        </main>`;
    }

    return `
      ${headerWithBack("📦 Cargar tenencias viejas", "/welcome")}
      <main>
        <section>
          <div class="card compact muted" style="font-size:13px;">
            Cargá tu portfolio <b>como estaba</b> en la fecha que elijas
            (saldos de apertura). El motor genera los asientos
            <code>OPENING_BALANCE</code> automáticamente.
            Después seguí cargando trades y movimientos normales.
          </div>
        </section>

        <form data-action="submitCargaInicial">
          <section>
            <h2>1. ¿Dónde y cuándo?</h2>
            <div class="field-row">
              <div class="field">
                <label>Cuenta destino *</label>
                <select name="account" required>
                  ${realAccounts.map(a =>
                    `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`
                  ).join("")}
                </select>
              </div>
              <div class="field">
                <label>Fecha del saldo *</label>
                <input type="date" name="fecha" value="${fmt.today()}" required>
              </div>
            </div>
            <div class="muted" style="font-size:12px;">
              Si tenés tenencias en varias cuentas, repetí este paso una
              vez por cuenta.
            </div>
          </section>

          <section>
            <h2>2. Posiciones</h2>
            <div id="carga-positions"></div>
            <button type="button" class="btn ghost full"
                    data-onclick="addCargaInicialRow" style="margin-bottom:10px;">
              ➕ Agregar posición
            </button>
            <div class="muted" style="font-size:12px;">
              Tickers existentes (<code>especies</code>): los reconoce solos.
              Si cargás uno nuevo, completá Asset class + Nombre.
              Para cash marcá la casilla y poné <code>ARS</code> /
              <code>USB</code> / <code>USDT</code> como ticker.
            </div>
          </section>

          <section>
            <button type="submit" class="btn primary full" style="margin-top:8px;">
              Guardar saldos de apertura
            </button>
            <div class="muted" style="font-size:11px; text-align:center; margin-top:6px;">
              Genera asientos OPENING_BALANCE en <code>asientos_contables</code>
              y re-importa el master.
            </div>
          </section>
        </form>
      </main>
      <script>
        // Auto-agregar la primera fila al renderizar
        setTimeout(() => {
          if (window._actions && window._actions.addCargaInicialRow) {
            window._actions.addCargaInicialRow();
          }
        }, 50);
      </script>
    `;
  });

  // ====================================================================
  // /credentials — Configurar credenciales BYMA / CAFCI
  // ====================================================================
  route("/credentials", async () => {
    let data;
    try {
      data = await API.getCredentials();
    } catch (e) {
      return `${headerWithBack("🔑 Credenciales", "/settings")}
        <main><div class="card">Error: ${escapeHtml(e.message)}</div></main>`;
    }
    const fields = data.fields || [];
    const configured = data.configured || {};
    const isSuper = !!data.is_superadmin;

    return `
      ${headerWithBack("🔑 Credenciales del broker", "/settings")}
      <main>
        <section>
          <div class="card compact" style="background: var(--green-soft); border-color: var(--green); color: var(--text);">
            <div style="font-size:13px;">
              🔒 Las credenciales se guardan <b>encriptadas</b> en el server.
              Nunca se devuelven por la API ni viajan en URLs ni se loguean en
              audit logs (solo el hash). Borralas en cualquier momento.
            </div>
          </div>
        </section>

        <section>
          <h2>BYMA / OMS (Cocos, LatinSecurities)</h2>
          <form data-action="saveCredentials">
            ${fields.map(f => `
              <div class="field">
                <label>
                  ${escapeHtml(f.label)}
                  ${configured[f.key] ? '<span class="tag success" style="font-size:9px; margin-left:4px;">configurado</span>' : ''}
                  ${f.superadmin_only ? '<span class="tag warn" style="font-size:9px; margin-left:4px;">superadmin</span>' : ''}
                </label>
                <input type="${f.secret ? "password" : "text"}"
                       name="${escapeHtml(f.key)}"
                       autocomplete="off"
                       placeholder="${configured[f.key] ? "(ya configurado — dejá vacío para no cambiar)" : ""}">
                <div class="muted" style="font-size:11px; margin-top:4px;">
                  ${escapeHtml(f.help)}
                </div>
              </div>
            `).join("")}
            <button type="submit" class="btn primary full" style="margin-top:12px;">
              Guardar credenciales
            </button>
          </form>
        </section>

        <section>
          <button class="btn danger full" data-onclick="deleteCredentials">
            🗑 Borrar todas las credenciales
          </button>
        </section>

        <section>
          <h2>Refrescar precios ahora</h2>
          <button class="btn ghost full" data-onclick="runByma" style="margin-bottom:8px;">
            🔄 Bajar precios BYMA (acciones, bonos, CEDEARs)
          </button>
          <div class="muted" style="font-size:12px; margin-bottom:14px;">
            Usa los tickers del archivo <code>tickers_byma.txt</code> del repo.
            Requiere credenciales BYMA configuradas.
          </div>

          <button class="btn ghost full" data-onclick="runCripto" style="margin-bottom:8px;">
            🪙 Bajar precios cripto (CoinGecko)
          </button>
          <div class="muted" style="font-size:12px; margin-bottom:14px;">
            BTC, ETH, SOL, USDT, USDC y más. API pública, sin credenciales.
            Los precios se comparten entre todos los users.
          </div>

          ${isSuper ? `
          <button class="btn ghost full" data-onclick="runCafci" style="margin-bottom:8px;">
            📊 Bajar VCP de FCIs (CAFCI) — solo superadmin
          </button>
          <div class="muted" style="font-size:12px;">
            Refresca <code>data/precios_cafci.csv</code> para todos los users.
            Requiere token CAFCI configurado arriba.
          </div>
          ` : `
          <div class="muted" style="font-size:12px;">
            Los precios de FCIs (CAFCI) los actualiza el superadmin —
            cuando lo haga, vas a verlos automáticamente en tu próximo
            re-import.
          </div>
          `}
        </section>
      </main>
      ${bottomNav("/settings")}
    `;
  });

  // ====================================================================
  // /audit — Audit log read-only
  // ====================================================================
  route("/audit", async () => {
    let data;
    try {
      data = await API.auditLog(200);
    } catch (e) {
      return `${headerWithBack("📜 Audit log", "/settings")}
        <main><div class="card">Error: ${escapeHtml(e.message)}</div></main>`;
    }
    const entries = (data.entries || []).slice().reverse();
    return `
      ${headerWithBack("📜 Audit log", "/settings")}
      <main>
        <section>
          <div class="muted" style="font-size:13px; margin-bottom:8px;">
            Últimas ${entries.length} operaciones que mutaron tu cuenta.
            Solo POST / PUT / DELETE. Body queda hasheado por privacidad.
          </div>
          ${entries.length === 0
            ? `<div class="card muted">Sin entradas todavía.</div>`
            : `<div class="list">${entries.map(e => `
              <div class="list-item" style="align-items: flex-start;">
                <div class="meta">
                  <div class="meta-line1">
                    <span class="tag ${e.status >= 400 ? 'danger' : 'success'}" style="font-size:9px;">${e.method}</span>
                    <span class="tabular" style="font-size:13px;">${escapeHtml(e.path)}</span>
                  </div>
                  <div class="meta-line2 muted" style="font-size:11px;">
                    ${escapeHtml(e.ts || "")} · ${escapeHtml(e.ip || "")}
                    ${e.is_switched ? " · <span class='tag warn'>switched</span>" : ""}
                    ${e.body_hash ? " · body=" + escapeHtml(e.body_hash) : ""}
                  </div>
                </div>
                <div class="right">
                  <span class="tabular" style="font-size:13px;">${e.status}</span>
                </div>
              </div>
            `).join("")}</div>`
          }
        </section>
      </main>
      ${bottomNav("/settings")}
    `;
  });

  // ====================================================================
  // /privacy + /terms — páginas estáticas
  // ====================================================================
  route("/privacy", async () => `
    ${headerWithBack("🔒 Privacidad", "/settings")}
    <main>
      <section><div class="card" style="line-height:1.55;">
        <h2 style="margin-bottom:8px;">Política de privacidad</h2>
        <p><b>Datos que guardamos</b><br/>
        Tu portfolio (cuentas, trades, gastos, ingresos), credenciales del
        broker (encriptadas), tu user_id y preferencias de la app.</p>
        <p style="margin-top:8px;"><b>Datos que NO guardamos</b><br/>
        No tocamos tu broker. Las credenciales se usan solo para que el
        loader baje precios — no realizamos órdenes ni transferencias.
        No compartimos data con terceros, no usamos analytics ni trackers
        externos.</p>
        <p style="margin-top:8px;"><b>Tus derechos (Ley 25.326 Argentina)</b><br/>
        Acceso, rectificación, supresión y portabilidad. Para portar:
        <i>Settings → Exportar todos mis datos</i>. Para borrar:
        <i>Settings → Borrar mis datos</i>.</p>
        <p style="margin-top:8px;"><b>Encriptación</b><br/>
        Las credenciales se encriptan con Fernet (AES-128 + HMAC) antes de
        escribirse a disk, usando una key derivada per-user.</p>
        <p style="margin-top:8px;"><b>Backups</b><br/>
        Cada modificación del Excel master genera un backup local
        (server-side). Se mantienen los últimos 30. No se replican fuera
        del server.</p>
        <p style="margin-top:8px;"><b>Audit log</b><br/>
        Toda mutación queda registrada en tu audit log (solo metadata,
        no body). Visible en <i>Settings → Audit log</i>.</p>
      </div></section>
    </main>
    ${bottomNav("/settings")}
  `);

  route("/terms", async () => `
    ${headerWithBack("📄 Términos", "/settings")}
    <main>
      <section><div class="card" style="line-height:1.55;">
        <h2 style="margin-bottom:8px;">Términos y condiciones</h2>
        <p><b>Naturaleza del servicio</b><br/>
        WM es una herramienta de seguimiento personal de portfolio. NO
        somos broker, NO somos asesor financiero, NO somos custodio de
        activos. Los datos se ingresan manualmente o vía API de tu broker.</p>
        <p style="margin-top:8px;"><b>Información indicativa</b><br/>
        Precios, FX, retornos y cualquier valoración es <i>indicativa</i> y
        puede tener delay. Para órdenes reales usá tu broker.</p>
        <p style="margin-top:8px;"><b>Sin garantías</b><br/>
        El servicio se provee "as-is". No garantizamos disponibilidad,
        precisión ni completitud. No nos hacemos responsables de pérdidas
        por errores de cálculo, datos faltantes o caídas del servicio.</p>
        <p style="margin-top:8px;"><b>Tu responsabilidad</b><br/>
        Vos sos responsable de la veracidad de los datos que cargás y de
        cumplir con tus obligaciones impositivas. WM NO te asesora.</p>
        <p style="margin-top:8px;"><b>Uso aceptable</b><br/>
        Una cuenta = un usuario humano. Prohibido scraping masivo,
        ingeniería inversa o reventa.</p>
        <p style="margin-top:8px;"><b>Cambios</b><br/>
        Estos términos pueden actualizarse. Si hacemos cambios materiales,
        avisamos vía la app antes del próximo login.</p>
      </div></section>
    </main>
    ${bottomNav("/settings")}
  `);

  // ====================================================================
  // /welcome — Onboarding wizard
  // ====================================================================
  route("/welcome", async () => {
    const meta = await loadMeta();
    let blotter = [];
    try {
      const r = await API.listSheet("blotter");
      blotter = r.items || [];
    } catch (_) {}
    let cuentasReales = (meta.accounts || [])
      .filter(c => !["caucion_pasivo", "caucion_activo"].some(p => c.startsWith(p)))
      .filter(c => !["external_income", "external_expense", "interest_income",
                     "interest_expense", "opening_balance"].includes(c));
    const hasAccounts = cuentasReales.length > 0;
    const hasTrades = (blotter || []).length > 0;
    const hasCreds = (await API.getCredentials().catch(() => ({}))).configured || {};
    const hasBymaCreds = !!hasCreds.byma_user;

    function step(n, title, desc, ctaLabel, ctaHref, done) {
      return `
        <div class="card" style="display:flex; gap:12px; align-items:flex-start; ${done ? 'opacity:0.55;' : ''}">
          <div style="font-size:24px; ${done ? "" : "filter: drop-shadow(0 0 4px var(--accent));"}">
            ${done ? "✅" : `<span class="ticker-suffix" style="font-size:14px; padding: 4px 10px;">${n}</span>`}
          </div>
          <div style="flex:1;">
            <div style="font-weight:700; margin-bottom:2px;">${escapeHtml(title)}</div>
            <div class="muted" style="font-size:13px; margin-bottom:8px;">${desc}</div>
            ${ctaHref && !done
              ? `<a href="${ctaHref}" class="btn primary" style="padding:8px 14px;">${escapeHtml(ctaLabel)}</a>`
              : ""}
            ${done ? `<span class="tag success">Listo</span>` : ""}
          </div>
        </div>
      `;
    }
    return `
      ${headerWithBack("🚀 Bienvenido", "/settings")}
      <main>
        <section>
          <div class="kpi primary" style="margin-bottom:14px;">
            <div class="kpi-label">Antes de arrancar</div>
            <div style="font-size:18px; font-weight:700; margin-top:4px;">
              Vamos a configurar tu portfolio en 4 pasos.
            </div>
            <div style="opacity:0.85; font-size:13px; margin-top:4px;">
              Si ya cargaste algo, los pasos completados aparecen tildados.
            </div>
          </div>

          ${step(1, "Crear cuentas",
            "Banco, broker (Cocos), wallet cripto, cash físico. Todo lo que tenga saldo.",
            "Ir a cuentas", "#/cuentas",
            hasAccounts)}

          ${step(2, "Cargar credenciales del broker",
            "Para que la app baje precios automáticamente desde BYMA. Opcional pero recomendado.",
            "Configurar", "#/credentials",
            hasBymaCreds)}

          ${step(3, "Cargar tus tenencias",
            "Si ya tenías posiciones, cargá los <b>saldos de apertura</b> a una fecha pasada (manual o auto-import). Después arrancan los trades normales.",
            "Cargar tenencias viejas", "#/carga-inicial",
            hasTrades)}

          <div class="card compact" style="margin: -6px 0 12px 0; background: var(--bg-soft);">
            <div class="muted" style="font-size:12px;">
              ¿Tenés API del broker? Auto-importá las posiciones desde
              <a href="#/import">📥 Auto-import</a>. Sino, cargá manual
              en el botón de arriba.
            </div>
          </div>

          ${step(4, "Refrescar precios y ver tu portfolio",
            "Apretá el botón de actualizar para que el motor recalcule todo.",
            "Ir al dashboard", "#/",
            hasAccounts && hasTrades)}
        </section>

        <section>
          <div class="card compact muted" style="font-size:12px;">
            ¿Algo no anda? Mirá <a href="#/help">Ayuda</a> o
            <a href="#/audit">el audit log</a>. Para soporte puntual el
            admin puede ver tu cuenta (read-only) si lo autorizás.
          </div>
        </section>
      </main>
      ${bottomNav("/settings")}
    `;
  });

  window.downloadAccountExport = async function () {
    try {
      toast("Generando export...", "info");
      const res = await fetch(`${API.base}/api/account/export`, {
        headers: { Authorization: `Bearer ${API.token()}` }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `wm-export-${new Date().toISOString().slice(0, 10)}.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast("Export descargado ✓", "success");
    } catch (e) {
      toast(`Error: ${e.message}`, "error");
    }
  };

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
      <div class="card compact" style="background:#F8FAFC; padding: 8px; margin: 8px 0; font-size: 12px;">
        <div class="muted" style="margin-bottom: 4px;">🎯 Tesis del trade (opcional, solo BUY)</div>
        <div class="field-row">
          ${inputField("Precio Target (TP)", "Precio Target",
                        row["Precio Target"], "number",
                        { placeholder: "ej 0.85" })}
          ${inputField("Stop Loss (SL)", "Stop Loss",
                        row["Stop Loss"], "number",
                        { placeholder: "ej 0.78" })}
        </div>
        ${selectField("Moneda Target", "Moneda Target",
                       row["Moneda Target"], meta.currencies,
                       { allowEmpty: true })}
      </div>
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
  function fundingFormFields(row, meta, isEdit) {
    const FUNDING_TIPOS = ["TOMA", "COLOCA"];
    const FUNDING_SUBTIPOS = ["CAUCION", "PASE", "PRESTAMO_FRANCES",
                                "PRESTAMO_BULLET", "PRESTAMO_AMERICANO"];
    const FUNDING_STATUS = ["OPEN", "CLOSED"];
    return `
      ${inputField("Fund ID *", "Fund ID", row["Fund ID"], "text",
                    { required: true, placeholder: "ej F0042" })}
      <div class="field-row">
        ${selectField("Tipo", "Tipo", row.Tipo, FUNDING_TIPOS,
                       { required: true, allowEmpty: false })}
        ${selectField("Subtipo", "Subtipo", row.Subtipo, FUNDING_SUBTIPOS,
                       { required: true, allowEmpty: false })}
      </div>
      ${selectField("Cuenta", "Cuenta", row.Cuenta, meta.accounts,
                     { required: true })}
      <div class="field-row">
        ${inputField("Fecha Inicio", "Fecha Inicio",
                      fmt.date(row["Fecha Inicio"]) || fmt.today(), "date",
                      { required: true })}
        ${inputField("Fecha Fin", "Fecha Fin",
                      fmt.date(row["Fecha Fin"]), "date")}
      </div>
      <div class="field-row">
        ${inputField("Monto", "Monto", row.Monto, "number", { required: true })}
        ${selectField("Moneda", "Moneda", row.Moneda, meta.currencies,
                       { required: true })}
      </div>
      <div class="field-row">
        ${inputField("TNA", "TNA", row.TNA, "number",
                      { placeholder: "0.24 o 24" })}
        ${inputField("Días", "Días", row.Días, "number",
                      { placeholder: "auto si dejás vacío" })}
      </div>
      ${selectField("Status", "Status", row.Status || "OPEN", FUNDING_STATUS,
                     { required: true, allowEmpty: false })}
      ${inputField("Linked Trade ID", "Linked Trade ID", row["Linked Trade ID"],
                    "text", { placeholder: "ej T0042 (opcional)" })}
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
  const ASSET_CLASSES = [
    "CASH",
    "BOND_AR", "BOND_CORP_AR", "BOND_US",
    "EQUITY_AR", "EQUITY_US", "EQUITY_GLOBAL",
    "ETF", "REIT", "FCI",
    "CRYPTO", "STABLECOIN",
    "DERIVATIVE", "COMMODITY", "REAL_ESTATE", "PRIVATE",
    "OTHER",
  ];
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
        <div class="actions">
          <button class="theme-toggle" data-onclick="toggleTheme" title="Cambiar tema">${themeIcon()}</button>
        </div>
      </div>
    `;
  }
  function emptyState(title, msg) {
    return `<div class="empty"><div class="icon">📭</div><div><b>${escapeHtml(title)}</b></div><div class="muted" style="margin-top:4px">${escapeHtml(msg)}</div></div>`;
  }

  // Bottom nav: tab bar fija con accesos rápidos. Marcamos como activa la
  // ruta que matchea el hash actual.
  function bottomNav(activePath) {
    const items = [
      { href: "/",         label: "Home",   icon: "🏠" },
      { href: "/trades",   label: "Trades", icon: "📈" },
      { href: "/cash",     label: "Cash",   icon: "💵" },
      { href: "/flows",    label: "Flujos", icon: "💸" },
      { href: "/settings", label: "Setup",  icon: "⚙️" },
    ];
    return `
      <nav class="bottom-nav">
        ${items.map(it => {
          const active = (activePath || "/") === it.href ? " active" : "";
          return `<a href="#${it.href}" class="bnav-item${active}">
            <span class="bnav-icon">${it.icon}</span>
            <span class="bnav-label">${it.label}</span>
          </a>`;
        }).join("")}
      </nav>
    `;
  }

  // -------- Bootstrap --------

  // Si no hay token → login screen.
  // Soporta dos modos: 'login' (default) y 'signup'. Hash router de
  // estados internos: #/forgot, #/reset?token=..., #/verify?token=...
  function showLogin() {
    function paint() {
      const hash = (window.location.hash || "").replace(/^#/, "");
      // Forzar estado por hash si el user vino con un link de email
      if (hash.startsWith("/reset-password")) return paintReset(hash);
      if (hash.startsWith("/verify-email"))   return paintVerify(hash);
      if (hash === "/forgot-password")        return paintForgot();
      if (hash === "/signup")                 return paintAuthForm("signup");
      return paintAuthForm("login");
    }

    function paintAuthForm(mode) {
      const isSignup = mode === "signup";
      document.body.innerHTML = `
        <div class="login-screen">
          <div class="logo">WM</div>
          <div class="tagline">Wealth Management</div>
          <form id="auth-form" autocomplete="on">
            <div class="field">
              <label>Email</label>
              <input type="email" name="email" required autocomplete="email"
                     placeholder="vos@ejemplo.com">
            </div>
            <div class="field">
              <label>Contraseña</label>
              <input type="password" name="password" required
                     autocomplete="${isSignup ? 'new-password' : 'current-password'}"
                     minlength="8" placeholder="8+ chars, letras + números">
            </div>
            ${isSignup ? `
              <div class="field">
                <label>Nombre (opcional)</label>
                <input type="text" name="display_name" autocomplete="name"
                       placeholder="¿Cómo te llamamos?">
              </div>
            ` : ""}
            <button type="submit" class="btn primary full" style="margin-top:12px">
              ${isSignup ? "Crear cuenta" : "Entrar"}
            </button>
            <div style="text-align:center; margin-top:14px; font-size:13px;">
              ${isSignup
                ? `¿Ya tenés cuenta? <a href="#/" style="color:#fff;text-decoration:underline;">Entrar</a>`
                : `¿Sin cuenta? <a href="#/signup" style="color:#fff;text-decoration:underline;">Crear una</a>`}
            </div>
            <div style="text-align:center; margin-top:6px; font-size:12px; opacity:0.85;">
              <a href="#/forgot-password" style="color:#fff;text-decoration:underline;">
                Olvidé mi contraseña
              </a>
            </div>
            ${!isSignup ? `
              <details style="margin-top:14px; opacity:0.7; font-size:11px;">
                <summary style="cursor:pointer;">Tengo un API token (modo legacy)</summary>
                <input id="legacy-token" type="password"
                       placeholder="bearer token de 64 chars"
                       style="margin-top:8px; width:100%; padding:10px;">
                <button type="button" id="legacy-go" class="btn ghost full" style="margin-top:6px; background:rgba(255,255,255,0.1); color:#fff;">Entrar con token</button>
              </details>
            ` : ""}
          </form>
        </div>
      `;
      document.getElementById("auth-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(e.target);
        const body = Object.fromEntries(fd.entries());
        try {
          if (isSignup) {
            const r = await API.req("/api/auth/signup",
              { method: "POST", json: body, skipAuth: true });
            if (r.verify_token_dev) {
              // Bootstrap superadmin con auto-verify: usamos el token directamente
              await API.req("/api/auth/verify-email",
                { method: "POST", json: { token: r.verify_token_dev },
                  skipAuth: true });
              toast("Cuenta creada y verificada (modo dev) ✓", "success");
            } else {
              toast(`Cuenta creada. Te mandamos un email a ${body.email} ` +
                    `para verificarla (revisá spam).`, "success");
            }
            // Auto-login
            const login = await API.req("/api/auth/login",
              { method: "POST", json: { email: body.email, password: body.password },
                skipAuth: true });
            API.setToken(login.session_token);
            window.location.hash = "/welcome";
            window.location.reload();
          } else {
            const r = await API.req("/api/auth/login",
              { method: "POST", json: body, skipAuth: true });
            API.setToken(r.session_token);
            if (!r.user.email_verified) {
              toast("Recordá verificar tu email para asegurar tu cuenta", "info");
            }
            window.location.hash = "";
            window.location.reload();
          }
        } catch (err) {
          toast(err.message || "Error", "error");
        }
      });
      // Legacy token entry
      const legacyBtn = document.getElementById("legacy-go");
      if (legacyBtn) {
        legacyBtn.addEventListener("click", async () => {
          const tok = document.getElementById("legacy-token").value.trim();
          if (!tok) return;
          API.setToken(tok);
          try {
            await API.req("/api/config", { noCache: true });
            window.location.reload();
          } catch (err) {
            toast("Token inválido: " + err.message, "error");
            API.setToken("");
          }
        });
      }
    }

    function paintForgot() {
      document.body.innerHTML = `
        <div class="login-screen">
          <div class="logo">WM</div>
          <div class="tagline">Recuperar contraseña</div>
          <form id="forgot-form">
            <div class="field">
              <label>Email</label>
              <input type="email" name="email" required autocomplete="email"
                     placeholder="vos@ejemplo.com">
            </div>
            <button type="submit" class="btn primary full" style="margin-top:12px">
              Mandarme link
            </button>
            <div style="text-align:center; margin-top:14px; font-size:13px;">
              <a href="#/" style="color:#fff;text-decoration:underline;">Volver al login</a>
            </div>
          </form>
        </div>
      `;
      document.getElementById("forgot-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const email = e.target.email.value.trim();
        try {
          await API.req("/api/auth/forgot-password",
            { method: "POST", json: { email }, skipAuth: true });
          toast("Si el email existe te mandamos un link. Revisá spam también.",
                "success");
          setTimeout(() => { window.location.hash = "/"; paint(); }, 1200);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    }

    function paintReset(hash) {
      const qs = hash.split("?")[1] || "";
      const params = new URLSearchParams(qs);
      const token = params.get("token") || "";
      document.body.innerHTML = `
        <div class="login-screen">
          <div class="logo">WM</div>
          <div class="tagline">Cambiar contraseña</div>
          <form id="reset-form">
            <div class="field">
              <label>Nueva contraseña</label>
              <input type="password" name="new_password" required
                     minlength="8" autocomplete="new-password">
            </div>
            <div class="field">
              <label>Confirmar</label>
              <input type="password" name="confirm" required minlength="8">
            </div>
            <button type="submit" class="btn primary full" style="margin-top:12px">
              Cambiar contraseña
            </button>
          </form>
        </div>
      `;
      document.getElementById("reset-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const np = e.target.new_password.value;
        const c = e.target.confirm.value;
        if (np !== c) { toast("Las contraseñas no coinciden", "error"); return; }
        try {
          await API.req("/api/auth/reset-password",
            { method: "POST", json: { token, new_password: np }, skipAuth: true });
          toast("Contraseña cambiada ✓ Ya podés entrar", "success");
          setTimeout(() => { window.location.hash = "/"; paint(); }, 1200);
        } catch (err) {
          toast(err.message, "error");
        }
      });
    }

    function paintVerify(hash) {
      const qs = hash.split("?")[1] || "";
      const params = new URLSearchParams(qs);
      const token = params.get("token") || "";
      document.body.innerHTML = `
        <div class="login-screen">
          <div class="logo">WM</div>
          <div class="tagline">Verificando email...</div>
          <div id="verify-status" class="loading" style="color:#fff; opacity:0.85;">
            <div class="spinner"></div> Esperá un toque...
          </div>
        </div>
      `;
      (async () => {
        try {
          await API.req("/api/auth/verify-email",
            { method: "POST", json: { token }, skipAuth: true });
          document.getElementById("verify-status").innerHTML =
            "✓ Email verificado. Ya podés entrar.";
          toast("Email verificado ✓", "success");
          setTimeout(() => { window.location.hash = "/"; paint(); }, 1500);
        } catch (err) {
          document.getElementById("verify-status").innerHTML =
            "✗ Link inválido o expirado.";
          toast(err.message, "error");
        }
      })();
    }

    paint();
    window.addEventListener("hashchange", paint);
  }

  function bootstrap() {
    // Hashes que vienen por email (reset / verify) siempre van por showLogin
    // — si el user tiene token de otra cuenta, igual queremos procesar el link.
    const hash = (window.location.hash || "").replace(/^#/, "");
    const isAuthHash = (hash.startsWith("/reset-password")
                         || hash.startsWith("/verify-email")
                         || hash === "/forgot-password"
                         || hash === "/signup");
    if (!API.token() || isAuthHash) {
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
