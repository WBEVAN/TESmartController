(() => {
  const $ = (sel) => document.querySelector(sel);
  const deck = $("#deck");
  const bodyEl = document.body;
  const nameTrack = $("#name-track");
  const statusEl = $("#status");
  const detailEl = $("#detail");
  const digits = $("#digits");
  const grid = $("#grid");
  const miniNav = $("#mini-nav");
  const viewBtn = $("#view-mode");
  const viewLabel = $("#view-mode-label");
  const msgBtn = $("#btn-message");
  const listBtn = $("#btn-message-list");
  const dimBtn = $("#lcd-dim-indicator");
  const historyDialog = $("#message-history-dialog");
  const historyList = $("#message-history-list");
  const nameEl = $("#name");

  const VIEWS = ["compact", "mini", "full"];
  const LCD_THEMES = ["green", "blue", "red", "amber"];
  const LCD_BRIGHT_FLOOR = 40;
  const HISTORY_MAX = 10;
  const NAMES = {
    1: "Office",
    2: "Mac Mini",
    3: "Apple TV",
    4: "Game",
    5: "Camera",
    6: "Spare",
    7: "NUC",
    8: "Pi",
  };
  const DEMO_HISTORY = [
    { text: "Door unlocked", sticky: false, seconds: 8, from: "HUBITAT" },
    { text: "Meeting in 5", sticky: false, seconds: 12, from: "HUBITAT" },
    { text: "Stay on camera", sticky: true, seconds: 8, from: "HUBITAT" },
    { text: "Visitor at gate", sticky: false, seconds: 10, from: "MESSAGE" },
    { text: "HVAC set to Eco", sticky: false, seconds: 8, from: "MESSAGE" },
    { text: "Package delivered", sticky: false, seconds: 15, from: "MESSAGE" },
    { text: "Garage closed", sticky: false, seconds: 6, from: "MESSAGE" },
    { text: "Motion: driveway", sticky: false, seconds: 8, from: "MESSAGE" },
    { text: "Lights: Scene Night", sticky: false, seconds: 10, from: "MESSAGE" },
    { text: "Alarm armed", sticky: true, seconds: 8, from: "MESSAGE" },
  ];

  const state = {
    count: 8,
    active: 3,
    view: "compact",
    body: "black",
    lcd: "green",
    brightness: 100,
    dimEnabled: false,
    dimmed: false,
    dimSeconds: 8,
    dimTimer: 0,
    banner: null,
    bannerUntil: 0,
    bannerTimer: 0,
    history: [],
    nextId: 1,
    showNames: false,
    hideDisabled: false,
  };

  const DISABLED = new Set([6]);

  function postHeight() {
    const height = Math.ceil(document.documentElement.scrollHeight);
    parent.postMessage({ type: "kit-demo-size", height, view: state.view }, "*");
  }

  function setEffectiveBrightness(level) {
    const n = Math.min(100, Math.max(LCD_BRIGHT_FLOOR, Number(level) || 100));
    const factor = (n / 100).toFixed(2);
    bodyEl.style.setProperty("--kit-lcd-brightness", factor);
    deck.style.setProperty("--kit-lcd-brightness", factor);
  }

  function syncDimButton() {
    if (!dimBtn) return;
    dimBtn.setAttribute("aria-pressed", String(state.dimEnabled));
    const label = state.dimEnabled
      ? "Auto-dim on. Click to disable."
      : "Auto-dim off. Click to enable.";
    dimBtn.title = label;
    dimBtn.setAttribute("aria-label", label);
  }

  function clearDimTimer() {
    if (state.dimTimer) {
      clearTimeout(state.dimTimer);
      state.dimTimer = 0;
    }
  }

  function enterDim() {
    state.dimTimer = 0;
    if (!state.dimEnabled) return;
    state.dimmed = true;
    setEffectiveBrightness(LCD_BRIGHT_FLOOR);
  }

  function noteActivity() {
    if (state.dimmed) {
      state.dimmed = false;
      setEffectiveBrightness(state.brightness);
    }
    clearDimTimer();
    if (!state.dimEnabled) return;
    state.dimTimer = setTimeout(enterDim, state.dimSeconds * 1000);
  }

  function setBrightness(level) {
    const n = Number(level);
    state.brightness = Number.isFinite(n)
      ? Math.min(100, Math.max(LCD_BRIGHT_FLOOR, Math.round(n / 5) * 5))
      : 100;
    state.dimmed = false;
    setEffectiveBrightness(state.brightness);
    noteActivity();
  }

  function setDimEnabled(on) {
    state.dimEnabled = Boolean(on);
    syncDimButton();
    if (state.dimEnabled) {
      noteActivity();
      return;
    }
    clearDimTimer();
    if (state.dimmed) {
      state.dimmed = false;
      setEffectiveBrightness(state.brightness);
    }
  }

  function setBody(body) {
    state.body = body === "silver" ? "silver" : "black";
    bodyEl.dataset.body = state.body;
    deck.dataset.body = state.body;
    postHeight();
  }

  function setLcd(lcd) {
    state.lcd = LCD_THEMES.includes(lcd) ? lcd : "green";
    bodyEl.dataset.lcd = state.lcd;
    deck.dataset.lcd = state.lcd;
    postHeight();
  }

  function setView(view) {
    const mode = VIEWS.includes(view) ? view : "compact";
    state.view = mode;
    deck.classList.remove("compact", "mini", "full");
    if (mode === "compact") deck.classList.add("compact");
    if (mode === "mini") deck.classList.add("mini");
    deck.dataset.mode = mode;
    viewBtn.dataset.mode = mode;
    viewLabel.textContent = mode === "full" ? "Full" : mode[0].toUpperCase() + mode.slice(1);
    viewBtn.setAttribute("aria-label", `View: ${viewLabel.textContent}`);
    miniNav.hidden = mode !== "mini";
    requestAnimationFrame(() => {
      postHeight();
      setTimeout(postHeight, 80);
    });
  }

  function clearBannerTimer() {
    if (state.bannerTimer) {
      clearTimeout(state.bannerTimer);
      state.bannerTimer = 0;
    }
  }

  function pushHistory(entry) {
    state.history = [entry, ...state.history.filter((item) => item.text !== entry.text)].slice(0, HISTORY_MAX);
  }

  function showBanner(text, { sticky = false, seconds = 8, from = "HUBITAT", fromHistory = false } = {}) {
    const line = String(text || "").trim().slice(0, 160);
    if (!line) return;
    clearBannerTimer();
    const fromLabel = String(from || "MESSAGE").trim() || "MESSAGE";
    const entry = {
      id: state.nextId++,
      text: line,
      from: fromLabel,
      sticky: Boolean(sticky),
      seconds: Math.max(1, Math.min(60, Number(seconds) || 8)),
      mode: sticky ? "sticky" : "timed",
    };
    state.banner = entry;
    if (!fromHistory) pushHistory(entry);
    if (!entry.sticky) {
      state.bannerUntil = performance.now() + entry.seconds * 1000;
      state.bannerTimer = setTimeout(() => {
        state.banner = null;
        draw();
      }, entry.seconds * 1000);
    } else {
      state.bannerUntil = 0;
    }
    noteActivity();
    draw();
  }

  function hideBanner() {
    clearBannerTimer();
    state.banner = null;
    noteActivity();
    draw();
  }

  function openHistory() {
    historyList.replaceChildren();
    if (!state.history.length) {
      const empty = document.createElement("p");
      empty.className = "kit-dialog-hint";
      empty.textContent = "No messages yet.";
      historyList.append(empty);
    } else {
      state.history.forEach((entry) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "message-history-item";
        btn.setAttribute("role", "option");
        if (state.banner && state.banner.id === entry.id) btn.classList.add("is-active");
        const text = document.createElement("span");
        text.className = "message-history-text";
        text.textContent = entry.text;
        const meta = document.createElement("span");
        meta.className = "message-history-meta";
        const from = entry.from || "MESSAGE";
        meta.textContent =
          entry.mode === "sticky" ? `${from} · sticky` : `${from} · ${entry.seconds}s`;
        btn.append(text, meta);
        btn.addEventListener("click", () => {
          closeHistory();
          showBanner(entry.text, {
            sticky: entry.mode === "sticky",
            seconds: entry.seconds,
            from: entry.from,
            fromHistory: true,
          });
        });
        historyList.append(btn);
      });
    }
    historyDialog.hidden = false;
    noteActivity();
    postHeight();
  }

  function closeHistory() {
    historyDialog.hidden = true;
    noteActivity();
    postHeight();
  }

  function draw() {
    const banner = state.banner;
    const showing = Boolean(banner);
    const hasHistory = state.history.length > 0;
    nameEl.classList.toggle("is-banner", showing);
    listBtn.hidden = !hasHistory;
    msgBtn.hidden = !hasHistory;
    msgBtn.classList.toggle("is-active", showing);
    if (showing) {
      nameTrack.textContent = banner.text;
      statusEl.textContent = banner.from || "HUBITAT";
      detailEl.textContent = banner.sticky
        ? "UNTIL DISMISSED"
        : `${Math.max(1, Math.ceil((state.bannerUntil - performance.now()) / 1000))} SEC`;
    } else {
      const label = NAMES[state.active] || `HDMI ${state.active}`;
      nameTrack.textContent = `${String(state.active).padStart(2, "0")}  ${label.toUpperCase()}`;
      statusEl.textContent = `INPUT ${state.active} SELECTED • DEMO`;
      detailEl.textContent = "Ready • PEEK OFF";
    }
    digits.textContent = "--";
    syncDimButton();

    bodyEl.classList.toggle("show-names", state.showNames);
    grid.replaceChildren();
    for (let n = 1; n <= state.count; n++) {
      const skipped = DISABLED.has(n);
      if (state.hideDisabled && skipped) continue;
      const named = NAMES[n] || "";
      const b = document.createElement("button");
      b.type = "button";
      b.className = "input" + (n === state.active ? " selected" : "") + (skipped ? " is-disabled" : "");
      const face = document.createElement("span");
      face.className = "input-face";
      face.textContent = state.showNames && named ? named : String(n);
      b.append(face);
      b.setAttribute("aria-pressed", String(n === state.active));
      b.setAttribute("aria-disabled", String(skipped));
      b.setAttribute("aria-label", `Input ${n}: ${named || "HDMI " + n}`);
      b.title = skipped
        ? state.showNames && named
          ? `${named} (input ${n}, disabled)`
          : `Input ${n} (disabled)`
        : state.showNames && named
          ? `${named} (input ${n})`
          : named || `Input ${n}`;
      if (!skipped) {
        b.addEventListener("click", () => {
          state.active = n;
          noteActivity();
          draw();
        });
      }
      grid.append(b);
    }
    postHeight();
  }

  viewBtn.addEventListener("click", () => {
    const i = VIEWS.indexOf(state.view);
    setView(VIEWS[(i + 1) % VIEWS.length]);
    noteActivity();
    draw();
  });

  $("#mini-prev").addEventListener("click", () => {
    state.active = state.active === 1 ? state.count : state.active - 1;
    noteActivity();
    draw();
  });
  $("#mini-next").addEventListener("click", () => {
    state.active = state.active === state.count ? 1 : state.active + 1;
    noteActivity();
    draw();
  });

  msgBtn.addEventListener("click", () => {
    if (state.banner) {
      hideBanner();
      return;
    }
    const last = state.history[0];
    if (last) {
      showBanner(last.text, {
        sticky: last.mode === "sticky",
        seconds: last.seconds,
        from: last.from,
        fromHistory: true,
      });
    }
  });

  listBtn.addEventListener("click", () => openHistory());
  $("#message-history-close").addEventListener("click", () => closeHistory());
  historyDialog.addEventListener("click", (event) => {
    if (event.target === historyDialog) closeHistory();
  });
  if (dimBtn) {
    dimBtn.addEventListener("click", () => setDimEnabled(!state.dimEnabled));
  }

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "kit-demo-view") setView(data.view);
    if (data.type === "kit-demo-body") setBody(data.body);
    if (data.type === "kit-demo-lcd") setLcd(data.lcd);
    if (data.type === "kit-demo-brightness") setBrightness(data.level);
    if (data.type === "kit-demo-auto-dim") setDimEnabled(Boolean(data.on));
    if (data.type === "kit-demo-names") {
      state.showNames = Boolean(data.on);
      noteActivity();
      draw();
    }
    if (data.type === "kit-demo-hide-disabled") {
      state.hideDisabled = Boolean(data.on);
      noteActivity();
      draw();
    }
    if (data.type === "kit-demo-banner") {
      showBanner(data.text, {
        sticky: data.sticky,
        seconds: data.seconds,
        from: data.from || "HUBITAT",
      });
    }
    if (data.type === "kit-demo-dismiss") hideBanner();
    if (data.type === "kit-demo-ping") postHeight();
  });

  window.addEventListener("resize", postHeight);
  setView("compact");
  setBody("black");
  setLcd("green");
  setBrightness(100);
  syncDimButton();
  DEMO_HISTORY.forEach((item) => {
    pushHistory({
      id: state.nextId++,
      text: item.text,
      from: item.from || "MESSAGE",
      sticky: item.sticky,
      seconds: item.seconds,
      mode: item.sticky ? "sticky" : "timed",
    });
  });
  draw();
  showBanner("Door unlocked", { seconds: 8, from: "HUBITAT" });
  setTimeout(postHeight, 120);
})();
