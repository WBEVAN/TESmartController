/* Local file:// boot deck — paints without a listener, then hands off to live /kit.
   Status text is ephemeral (never banners / kit.json). */
(() => {
  const $ = (s) => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  const target = params.get("target") || "";
  const discover = params.has("discover");
  const waitingForAdvertise = discover && (!target || target.includes("127.0.0.1:9"));

  if (params.has("transparent")) {
    document.body.classList.add("is-transparent");
  }

  const led = {
    offline: ["svg/led-offline.svg", "offline"],
    connected: ["svg/led-connected.svg", "online"],
    busy: ["svg/led-busy.svg", "busy"],
    error: ["svg/led-error.svg", "error"],
  };

  function setLed(kind) {
    const [src, label] = led[kind] || led.offline;
    const el = $("#led");
    const img = $("#led-img");
    const text = $("#led-label");
    if (el) {
      el.dataset.state = kind;
      el.title = label;
    }
    if (img) {
      img.src = src;
      img.alt = label;
    }
    if (text) text.textContent = label;
  }

  function makeUnit(text, { clone = false } = {}) {
    const unit = document.createElement("span");
    unit.className = "name-unit";
    if (clone) unit.setAttribute("aria-hidden", "true");
    const icon = document.createElement("span");
    icon.className = "name-unit-icon";
    icon.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.className = "name-unit-text";
    label.textContent = text;
    unit.append(icon, label);
    return unit;
  }

  function setMarquee(text) {
    const nameRoot = $("#name");
    const track = $("#name-track");
    if (!nameRoot || !track) return;
    const key = `boot:${text}`;
    if (track.dataset.key === key) return;
    track.dataset.key = key;
    track.replaceChildren(makeUnit(text), makeUnit(text, { clone: true }));
    nameRoot.classList.add("is-banner", "is-boot");
    requestAnimationFrame(() => {
      const unit = track.querySelector(".name-unit");
      if (!unit) return;
      nameRoot.classList.remove("is-scrolling");
      const segment = unit.getBoundingClientRect().width || nameRoot.clientWidth || 200;
      nameRoot.classList.add("is-scrolling");
      track.style.setProperty("--lcd-scroll-distance", `${segment}px`);
      track.style.setProperty("--lcd-scroll-duration", `${Math.max(4, Math.min(18, segment / 36))}s`);
    });
  }

  function setStatus(phase, detail, ledKind) {
    setMarquee(phase);
    const status = $("#status");
    const detailEl = $("#detail");
    if (status) status.textContent = "STARTING";
    if (detailEl) detailEl.textContent = detail || phase;
    if (ledKind) setLed(ledKind);
  }

  function paintGrid() {
    const grid = $("#grid");
    if (!grid) return;
    grid.replaceChildren();
    for (let n = 1; n <= 8; n += 1) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "input";
      b.disabled = true;
      b.title = `Input ${n}`;
      b.innerHTML =
        `<span class="num">${String(n).padStart(2, "0")}</span>` +
        `<span class="label">INPUT ${n}</span>`;
      grid.append(b);
    }
  }

  function pyApi() {
    try {
      return window.pywebview && window.pywebview.api;
    } catch {
      return null;
    }
  }

  function hostLabel() {
    if (waitingForAdvertise || !target) return "listener";
    try {
      const u = new URL(target);
      return u.host || target;
    } catch {
      return target;
    }
  }

  async function waitForApi(timeoutMs) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const api = pyApi();
      if (api && typeof api.probe_listener === "function") return api;
      await new Promise((r) => setTimeout(r, 50));
    }
    return pyApi();
  }

  async function applyPrefs(api) {
    if (!api || typeof api.get_prefs !== "function") return;
    try {
      const prefs = await api.get_prefs();
      if (!prefs || typeof prefs !== "object") return;
      const body = prefs.body === "silver" ? "silver" : "black";
      const lcd = ["green", "blue", "red", "amber"].includes(prefs.lcd) ? prefs.lcd : "green";
      const glass = ["matrix", "phosphor", "off"].includes(prefs.lcd_glass)
        ? prefs.lcd_glass
        : "matrix";
      let brightness = Number(prefs.lcd_brightness);
      if (!Number.isFinite(brightness)) brightness = 100;
      brightness = Math.min(100, Math.max(40, Math.round(brightness / 5) * 5));
      const brightFactor = (brightness / 100).toFixed(2);
      document.body.dataset.body = body;
      document.body.dataset.lcd = lcd;
      document.body.dataset.glass = glass;
      document.body.style.setProperty("--kit-lcd-brightness", brightFactor);
      const deck = $("#deck");
      if (deck) {
        deck.dataset.body = body;
        deck.dataset.lcd = lcd;
        deck.dataset.glass = glass;
        deck.style.setProperty("--kit-lcd-brightness", brightFactor);
      }
    } catch {
      /* ignore */
    }
  }

  function wireChrome(api) {
    const power = $("#btn-power");
    if (power) {
      power.onclick = () => {
        if (api && api.close) Promise.resolve(api.close()).catch(() => window.close());
        else window.close();
      };
    }
    const handle = $("#drag-handle") || $("#drag");
    if (!handle) return;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let dragChain = Promise.resolve();
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      dragging = true;
      lastX = e.screenX;
      lastY = e.screenY;
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dx = e.screenX - lastX;
      const dy = e.screenY - lastY;
      lastX = e.screenX;
      lastY = e.screenY;
      if (!dx && !dy) return;
      if (api && api.drag_by) {
        dragChain = dragChain
          .then(() => Promise.resolve(api.drag_by(dx, dy)))
          .catch(() => {});
      }
    });
    handle.addEventListener("pointerup", () => {
      dragging = false;
    });
    handle.addEventListener("pointercancel", () => {
      dragging = false;
    });
  }

  function syncWindowClip(api) {
    const deck = document.querySelector("#deck");
    if (!deck || !api || typeof api.set_window_clip !== "function") return;
    const radius = parseFloat(getComputedStyle(deck).borderTopLeftRadius);
    if (!Number.isFinite(radius) || radius < 1) return;
    Promise.resolve(api.set_window_clip(radius)).catch(() => {});
  }

  async function openLive(api) {
    setStatus("Opening kit…", hostLabel(), "busy");
    if (api && typeof api.load_live === "function") {
      await api.load_live();
      return;
    }
    if (target) location.replace(target);
  }

  async function probeLoop(api) {
    const where = hostLabel();
    let attempts = 0;
    for (;;) {
      attempts += 1;
      setStatus(
        attempts === 1 ? `Connecting to ${where}…` : `Waiting for ${where}…`,
        target || where,
        attempts > 2 ? "error" : "busy"
      );
      try {
        const result = api && typeof api.probe_listener === "function"
          ? await api.probe_listener()
          : { ok: false, detail: "Native bridge unavailable" };
        if (result && result.ok) {
          await openLive(api);
          return;
        }
        const detail = (result && result.detail) || "No response";
        setStatus(`Waiting for ${where}…`, detail, "error");
      } catch (err) {
        setStatus(`Waiting for ${where}…`, err.message || String(err), "error");
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }

  paintGrid();
  setStatus(
    target ? `Connecting to ${hostLabel()}…` : "No listener URL",
    target || "Pass target= in the boot URL",
    "busy"
  );

  (async () => {
    const api = await waitForApi(4000);
    wireChrome(api);
    await applyPrefs(api);
    syncWindowClip(api);
    if (!target && !discover) {
      setStatus("No listener URL", "Relaunch with tesmartctl kit", "error");
      return;
    }
    await probeLoop(api);
  })();
})();
