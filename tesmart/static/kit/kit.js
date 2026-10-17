/* Live floating kit — talks to the same /api routes as /panel. */
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];

  const state = {
    online: false,
    busy: false,
    active: null,
    count: 8,
    names: {},
    endpoint: "",
    mode: "off",
    peekSeconds: 5,
    peek: null, // { origin, target, end, seconds }
    message: "Connecting…",
    cycleList: [],
    cycling: false,
    cycleDue: 0,
  };

  const led = {
    offline: ["/static/kit/svg/led-offline.svg", "offline"],
    connected: ["/static/kit/svg/led-connected.svg", "online"],
    busy: ["/static/kit/svg/led-busy.svg", "busy"],
    error: ["/static/kit/svg/led-error.svg", "error"],
  };

  function setLed(kind) {
    const [src, label] = led[kind] || led.offline;
    $("#led").dataset.state = kind;
    $("#led-img").src = src;
    $("#led-label").textContent = label;
  }

  async function api(method, path, body) {
    const opts = { method, headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try {
      data = await res.json();
    } catch {
      data = {};
    }
    if (!res.ok) {
      const err = new Error((data && (data.error || data.message)) || `HTTP ${res.status}`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function draw() {
    const grid = $("#grid");
    grid.replaceChildren();
    for (let n = 1; n <= state.count; n++) {
      const b = document.createElement("button");
      b.className =
        "input" +
        (n === state.active ? " selected" : "") +
        (state.peek && n === state.peek.target ? " peeking" : "") +
        (state.peek && n === state.peek.origin ? " returning" : "");
      b.textContent = String(n);
      const label = state.names[String(n)] || state.names[n] || `Input ${n}`;
      b.setAttribute("aria-label", `Input ${n}: ${label}`);
      b.setAttribute("aria-pressed", String(n === state.active));
      b.disabled = !state.online || state.busy || !!state.peek;
      b.onclick = () => selectInput(n);
      b.oncontextmenu = (e) => {
        e.preventDefault();
        renameInput(n);
      };
      grid.append(b);
    }

    const activeName =
      state.active == null
        ? "—"
        : `${String(state.active).padStart(2, "0")}  ${state.names[String(state.active)] || state.names[state.active] || `INPUT ${state.active}`}`;
    $("#name").textContent = state.online ? activeName : "CONTROLLER OFFLINE";
    $("#status").textContent = state.peek
      ? `PEEK • RETURNING TO ${String(state.peek.origin).padStart(2, "0")}`
      : state.online
        ? `INPUT ${state.active} SELECTED`
        : "INPUT STATE UNKNOWN";
    $("#detail").textContent = state.peek
      ? "TEMPORARY INPUT"
      : `${state.message} • PEEK ${state.mode.toUpperCase()}`;

    $$("[data-mode]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode));
      b.disabled = state.busy || !!state.peek || !state.online;
    });
    $("#seconds").disabled = state.busy || !!state.peek;
    $("#prev").disabled = $("#next").disabled = !state.online || state.busy || !!state.peek;
    $("#cycle").disabled = !state.online || state.busy;
    $("#digits").textContent = state.peek
      ? String(Math.max(0, Math.ceil((state.peek.end - performance.now()) / 1000))).padStart(2, "0")
      : "--";
    $("#endpoint").textContent = state.endpoint || "";

    if (state.busy) setLed("busy");
    else if (!state.online) setLed("offline");
    else setLed("connected");
  }

  async function refresh() {
    try {
      const data = await api("GET", "/api/state?network=0");
      state.online = true;
      state.active = data.active_input ?? data.activeInput ?? state.active;
      state.count = data.input_count ?? data.inputCount ?? state.count;
      state.names = data.names || {};
      state.endpoint = data.endpoint || "";
      const peek = data.peek || {};
      if (typeof peek.seconds === "number") {
        state.peekSeconds = peek.seconds;
        $("#seconds").value = String(peek.seconds);
      }
      if (peek.follow) state.mode = String(peek.follow);
      const rotate = data.rotate || {};
      if (Array.isArray(rotate.cycle) && rotate.configured) {
        state.cycleList = rotate.cycle.map(Number);
        $("#cycle-inputs").value = state.cycleList.join(",");
      } else {
        state.cycleList = [];
        $("#cycle-inputs").value = "";
      }
      if (typeof rotate.seconds === "number") {
        $("#cycle-seconds").value = String(rotate.seconds);
      }
      if (!state.peek) state.message = "Ready";
      draw();
    } catch (err) {
      state.online = false;
      state.message = err.message || "Offline";
      setLed("error");
      draw();
    }
  }

  async function withBusy(fn) {
    if (state.busy) return;
    state.busy = true;
    draw();
    try {
      await fn();
    } catch (err) {
      state.message = err.message || "Request failed";
      setLed("error");
      await refresh();
    } finally {
      state.busy = false;
      draw();
    }
  }

  async function selectInput(n) {
    if (!state.online || state.busy || state.peek || n === state.active) return;
    await withBusy(async () => {
      if (state.mode === "off") {
        const data = await api("POST", "/api/input", { input: n });
        state.active = data.active_input ?? n;
        state.message = "Selected";
        return;
      }
      // Armed peek-follow: plain input change peeks via listener.
      state.message = "Peek requested";
      draw();
      const seconds = Math.max(1, Math.min(60, Number($("#seconds").value) || state.peekSeconds));
      const origin = state.active;
      state.peek = { origin, target: n, end: performance.now() + seconds * 1000, seconds };
      state.active = n;
      draw();
      try {
        const data = await api("POST", "/api/input", { input: n });
        // Follow mode may have peeked; settle from response when present.
        if (data.restored != null || data.peeked != null) {
          state.active = data.active_input ?? data.restored ?? origin;
          if (data.interrupted) state.message = "External selection retained";
          else state.message = "Returned to previous input";
          state.peek = null;
        } else {
          // Local estimate until response; refresh after timeout window.
          await new Promise((r) => setTimeout(r, seconds * 1000 + 200));
          await refresh();
          state.peek = null;
          state.message = "Peek finished";
        }
        if (state.mode === "once") {
          state.mode = "off";
          await api("POST", "/api/peek-follow", { mode: "off" });
        }
      } catch (err) {
        state.peek = null;
        throw err;
      }
    });
  }

  function openRenameDialog(n) {
    const dialog = $("#rename-dialog");
    const field = $("#rename-input");
    const title = $("#rename-title");
    const current = state.names[String(n)] || state.names[n] || "";
    if (!dialog || !field) return Promise.resolve(null);

    title.textContent = `Name input ${n}`;
    field.value = current;
    dialog.hidden = false;
    field.focus();
    field.select();

    return new Promise((resolve) => {
      const finish = (value) => {
        dialog.hidden = true;
        dialog.removeEventListener("keydown", onKey);
        $("#rename-save").onclick = null;
        $("#rename-cancel").onclick = null;
        $("#rename-clear").onclick = null;
        dialog.onclick = null;
        resolve(value);
      };
      const onKey = (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          finish(null);
        } else if (e.key === "Enter") {
          e.preventDefault();
          finish(field.value);
        }
      };
      dialog.addEventListener("keydown", onKey);
      $("#rename-save").onclick = () => finish(field.value);
      $("#rename-cancel").onclick = () => finish(null);
      $("#rename-clear").onclick = () => {
        field.value = "";
        field.focus();
      };
      dialog.onclick = (e) => {
        if (e.target === dialog) finish(null);
      };
    });
  }

  async function renameInput(n) {
    const next = await openRenameDialog(n);
    if (next === null) return;
    await withBusy(async () => {
      await api("POST", "/api/names", { names: { [String(n)]: next.trim() } });
      await refresh();
      state.message = next.trim() ? "Name saved" : "Name cleared";
    });
  }

  async function setPeekMode(mode) {
    await withBusy(async () => {
      const seconds = Math.max(1, Math.min(60, Number($("#seconds").value) || 5));
      if (mode === "off") await api("POST", "/api/peek-follow", { mode: "off" });
      else await api("POST", "/api/peek-follow", { mode, seconds });
      state.mode = mode;
      state.peekSeconds = seconds;
      state.message = `Peek ${mode}`;
      await refresh();
    });
  }

  function cycleStep(direction = 1) {
    const list =
      state.cycleList.length > 0
        ? state.cycleList
        : Array.from({ length: state.count }, (_, i) => i + 1);
    let i = list.indexOf(state.active);
    if (i < 0) return direction > 0 ? list[0] : list[list.length - 1];
    return list[(i + direction + list.length) % list.length];
  }

  function stopCycle() {
    state.cycling = false;
    $("#cycle").setAttribute("aria-pressed", "false");
    $("#cycle-label").textContent = "Start cycle";
  }

  async function saveCycleConfig() {
    const seconds = Math.max(2, Number($("#cycle-seconds").value) || 15);
    const body = { mode: "manual", seconds };
    if (state.cycleList.length) body.inputs = state.cycleList;
    else body.inputs = [];
    await api("POST", "/api/cycle", body);
  }

  // --- UI wiring ------------------------------------------------------------

  $$("[data-mode]").forEach((b) => {
    b.onclick = () => setPeekMode(b.dataset.mode);
  });

  $("#seconds").onchange = async () => {
    const seconds = Math.max(1, Math.min(60, Number($("#seconds").value) || 5));
    $("#seconds").value = String(seconds);
    await withBusy(async () => {
      await api("POST", "/api/peek-default", { seconds });
      state.peekSeconds = seconds;
      state.message = `Peek default ${seconds}s`;
    });
  };

  $("#prev").onclick = () => selectInput(cycleStep(-1));
  $("#next").onclick = () => selectInput(cycleStep(1));

  $("#compact").onclick = () => {
    const compact = $(".panel").classList.toggle("compact");
    $("#compact").setAttribute("aria-pressed", String(compact));
    $("#compact span").textContent = compact ? "Expand" : "Compact";
    sizeWidgetToMode({ resizeWindow: true });
  };

  function setSetupDoor(open) {
    const bay = $("#service-bay");
    const door = $("#service-door");
    const inner = $("#service-bay-inner");
    const label = $("#door-label-text");
    if (!bay || !door || !inner) return;
    bay.dataset.open = open ? "true" : "false";
    door.setAttribute("aria-expanded", String(open));
    door.title = open ? "Close setup door" : "Open setup door";
    inner.hidden = !open;
    if (label) label.textContent = open ? "Close setup" : "Setup";
    try {
      localStorage.setItem("tesmart-kit-setup-door", open ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  (() => {
    let open = false;
    try {
      open = localStorage.getItem("tesmart-kit-setup-door") === "1";
    } catch {
      open = false;
    }
    setSetupDoor(open);
    $("#service-door").onclick = () => {
      const next = $("#service-bay").dataset.open !== "true";
      setSetupDoor(next);
      sizeWidgetToMode({ resizeWindow: true });
    };
  })();

  $("#cycle").onclick = async () => {
    if (state.cycling) {
      stopCycle();
      return;
    }
    if (!state.online) return;
    try {
      await saveCycleConfig();
    } catch (err) {
      $("#cycle-error").textContent = err.message || "Could not save cycle";
      return;
    }
    state.cycling = true;
    state.cycleDue =
      performance.now() + Math.max(2, Number($("#cycle-seconds").value) || 15) * 1000;
    $("#cycle").setAttribute("aria-pressed", "true");
    $("#cycle-label").textContent = "Stop cycle";
  };

  $("#cycle-inputs").onchange = () => {
    const raw = $("#cycle-inputs").value.trim();
    const list = raw ? raw.split(",").map((s) => Number(s.trim())) : [];
    if (list.some((n) => !Number.isInteger(n) || n < 1 || n > state.count)) {
      $("#cycle-error").textContent = `Use input numbers 1–${state.count}`;
      stopCycle();
      return;
    }
    state.cycleList = [...new Set(list)];
    $("#cycle-error").textContent = "";
    stopCycle();
    withBusy(async () => {
      await saveCycleConfig();
      state.message = "Cycle list saved";
    });
  };

  $("#cycle-seconds").onchange = () => {
    stopCycle();
    withBusy(async () => {
      await saveCycleConfig();
      state.message = "Cycle interval saved";
    });
  };

  setInterval(() => {
    if (state.peek) {
      const remaining = Math.max(0, (state.peek.end - performance.now()) / 1000);
      $("#digits").textContent = String(Math.ceil(remaining)).padStart(2, "0");
    }
    if (!state.cycling) return;
    if (!state.online || document.hidden) {
      stopCycle();
      return;
    }
    if (state.peek || state.busy) {
      state.cycleDue =
        performance.now() + Math.max(2, Number($("#cycle-seconds").value) || 15) * 1000;
      return;
    }
    if (performance.now() >= state.cycleDue) {
      selectInput(cycleStep(1));
      state.cycleDue =
        performance.now() + Math.max(2, Number($("#cycle-seconds").value) || 15) * 1000;
    }
  }, 200);

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopCycle();
  });

  // Chrome-less app window (Chromium --app / pywebview) or browser popup fallback.
  const params = new URLSearchParams(location.search);
  const isApp = params.has("app");
  const isPopup =
    isApp ||
    Boolean(window.opener) ||
    window.name === "tesmart-kit" ||
    params.has("popup");
  if (isPopup) {
    document.body.classList.add("is-popup");
  }
  if (isApp) {
    document.body.classList.add("is-app");
  }

  function openBrowserPopup() {
    const url = `${location.origin}/kit?popup=1`;
    const features = "popup=yes,width=756,height=700,resizable=yes";
    const win = window.open(url, "tesmart-kit", features);
    if (win) {
      try {
        win.focus();
      } catch {
        /* ignore */
      }
    }
  }

  $("#btn-popout").onclick = async () => {
    // Frameless pywebview (preferred) or Chromium --app on the listener host.
    try {
      await api("POST", "/api/kit-window", { prefer: "auto" });
      state.message = "Opened frameless window on the listener host";
      draw();
    } catch {
      openBrowserPopup();
    }
  };

  function pyApi() {
    return (window.pywebview && window.pywebview.api) || null;
  }

  const closeBtn = $("#btn-close");
  if (closeBtn) {
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const apiBridge = pyApi();
      if (apiBridge && apiBridge.close) {
        Promise.resolve(apiBridge.close()).catch(() => {
          try {
            window.close();
          } catch {
            /* ignore */
          }
        });
        return;
      }
      try {
        window.close();
      } catch {
        /* ignore */
      }
    });
  }

  let pinTimer = null;
  $("#btn-pin").onclick = async () => {
    const on = $("#btn-pin").getAttribute("aria-pressed") !== "true";
    $("#btn-pin").setAttribute("aria-pressed", String(on));
    clearInterval(pinTimer);
    const apiBridge = pyApi();
    if (apiBridge && apiBridge.pin) {
      try {
        await apiBridge.pin(on);
        return;
      } catch {
        /* fall through */
      }
    }
    if (on) {
      pinTimer = setInterval(() => {
        try {
          window.focus();
        } catch {
          /* ignore */
        }
      }, 1500);
    }
  };

  // Fit the OS/popup window to the deck (compact + setup door). Manual
  // resize then uniformly zooms the deck so controls never overlap.
  let ignoreResize = false;
  let resizeTimer = null;
  let measureBusy = false;

  function deckDesignSize() {
    const deck = $("#deck");
    const compact = deck.classList.contains("compact");
    const width = compact ? 380 : 720;
    const prevZoom = deck.style.zoom;
    const prevWidth = deck.style.width;
    deck.style.zoom = "1";
    deck.style.width = `${width}px`;
    // Force layout with door state already applied in the DOM.
    const height = Math.ceil(Math.max(deck.scrollHeight, deck.getBoundingClientRect().height));
    deck.style.width = prevWidth;
    deck.style.zoom = prevZoom;
    return { width, height: Math.max(280, height) };
  }

  function applyDeckZoom() {
    if ((!isApp && !isPopup) || measureBusy) return;
    const deck = $("#deck");
    if (!deck) return;
    const { width, height } = deckDesignSize();
    const availW = Math.max(1, window.innerWidth);
    const availH = Math.max(1, window.innerHeight);
    // Prefer fitting exactly; allow slight upscale when the user grows the window.
    const scale = Math.min(availW / width, availH / height);
    if (!Number.isFinite(scale) || scale <= 0) return;
    deck.style.zoom = String(Math.min(Math.max(scale, 0.5), 2));
  }

  function sizeWidgetToMode({ resizeWindow = false } = {}) {
    if (!isApp && !isPopup) return;
    const deck = $("#deck");
    if (!deck) return;

    const run = () => {
      measureBusy = true;
      deck.style.zoom = "1";
      const { width, height } = deckDesignSize();
      measureBusy = false;

      const finish = () => {
        ignoreResize = false;
        applyDeckZoom();
      };

      if (!resizeWindow) {
        applyDeckZoom();
        return;
      }

      ignoreResize = true;
      const apiBridge = pyApi();
      if (apiBridge && apiBridge.resize_to) {
        Promise.resolve(apiBridge.resize_to(width, height))
          .catch(() => {})
          .finally(() => setTimeout(finish, 80));
        return;
      }
      try {
        window.resizeTo(width, height);
      } catch {
        /* browser may block resizeTo */
      }
      setTimeout(finish, 80);
    };

    // Wait a frame so door open/close / compact class have laid out.
    requestAnimationFrame(() => requestAnimationFrame(run));
  }

  window.addEventListener("pywebviewready", () => {
    sizeWidgetToMode({ resizeWindow: true });
  });
  if (isApp || isPopup) {
    window.addEventListener("load", () => sizeWidgetToMode({ resizeWindow: true }));
    window.addEventListener("resize", () => {
      if (ignoreResize) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => applyDeckZoom(), 50);
    });
  }

  // Browser-popup drag only. Frameless pywebview uses native easy_drag /
  // pywebview-drag-region — custom move_by fights that and feels stuck.
  (() => {
    if (window.pywebview) return;
    const handle = $("#drag-handle") || $("#drag");
    if (!handle) return;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      if (e.target.closest("button, a, input, select, label")) return;
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
      try {
        window.moveBy(dx, dy);
      } catch {
        /* some browsers block moveBy outside script-opened popups */
      }
    });
    handle.addEventListener("pointerup", () => {
      dragging = false;
    });
    handle.addEventListener("pointercancel", () => {
      dragging = false;
    });
  })();

  draw();
  refresh();
  setInterval(() => {
    if (!state.busy && !state.peek) refresh();
  }, 5000);
})();
