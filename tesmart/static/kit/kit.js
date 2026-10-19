/* Live floating kit — talks to the same /api routes as /panel. */
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];

  const params = new URLSearchParams(location.search);
  // pywebview / Chromium --app / tesmartctl kit always pass ?app=1. Also
  // treat an injected pywebview bridge as app so chrome never flashes.
  const isApp = params.has("app") || Boolean(window.pywebview);
  const isPopup =
    isApp ||
    Boolean(window.opener) ||
    window.name === "tesmart-kit" ||
    params.has("popup");

  // Apply shell classes immediately — do not wait until the end of init
  // (a later throw used to leave Pop out / Full panel visible in the kit window).
  if (isPopup) document.body.classList.add("is-popup");
  if (isApp) {
    document.body.classList.add("is-app");
    document.documentElement.dataset.kitShell = "app";
  } else if (params.has("popup")) {
    document.documentElement.dataset.kitShell = "popup";
  }

  const prefs = new window.KitPrefs({ isApp, fresh: params.has("fresh") });

  const state = {
    online: false,
    busy: false,
    active: null,
    count: 8,
    realCount: 8,
    // From URL ?ports=16 (tesmartctl kit --ports 16); display-only override.
    forcedPorts: 0,
    names: {},
    endpoint: "",
    mode: "off",
    peekSeconds: 5,
    peek: null, // { origin, target, end, seconds }
    message: "Connecting…",
    cycleList: [],
    cycling: false,
    cycleDue: 0,
    banner: null, // displayed message; .showing can be local until the server dismisses/reshows
    bannerHistory: [], // up to 10 from server, newest first
    lastBroadcastId: null, // last server banner id this kit has adopted
    lastServerShowing: null, // last poll's banner.showing — detects dismiss / reshow
    bannerTimer: 0,
    sse: false, // true while EventSource to /api/events is open
    eventSource: null,
    pollTimer: 0,
    // Ephemeral LCD boot/reconnect ticker — never written to banner history or kit.json.
    bootPhase: null,
    bootClearTimer: 0,
  };

  const POLL_MS = 5000;
  const POLL_SSE_MS = 30000;
  // While the switch is unreachable, probe often — SSE to the listener stays
  // healthy even when the HDMI box is off, so we cannot wait on the slow poll.
  const POLL_OFFLINE_MS = 2000;

  (() => {
    const forced = Number(params.get("ports") || params.get("inputs") || 0);
    if (Number.isInteger(forced) && forced >= 8 && forced <= 32) {
      state.forcedPorts = forced;
      state.count = forced;
    }
  })();

  // Match the server's ?v= stamp so LED swaps hit the same HTTP cache bucket.
  const assetVer = document.documentElement.dataset.kitVersion || "";
  const assetUrl = (path) => (assetVer ? `${path}?v=${encodeURIComponent(assetVer)}` : path);
  const led = {
    offline: [assetUrl("/static/kit/svg/led-offline.svg"), "offline"],
    connected: [assetUrl("/static/kit/svg/led-connected.svg"), "online"],
    busy: [assetUrl("/static/kit/svg/led-busy.svg"), "busy"],
    error: [assetUrl("/static/kit/svg/led-error.svg"), "error"],
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

  function displayCount() {
    return state.forcedPorts || state.realCount || state.count || 8;
  }

  function draw() {
    const grid = $("#grid");
    if (!grid) return;
    grid.replaceChildren();
    const shown = displayCount();
    for (let n = 1; n <= shown; n++) {
      const b = document.createElement("button");
      const beyond = n > state.realCount;
      b.className =
        "input" +
        (n === state.active ? " selected" : "") +
        (state.peek && n === state.peek.target ? " peeking" : "") +
        (state.peek && n === state.peek.origin ? " returning" : "") +
        (beyond ? " preview-only" : "");
      b.textContent = String(n);
      const label = state.names[String(n)] || state.names[n] || `Input ${n}`;
      b.setAttribute("aria-label", beyond ? `Input ${n} (preview only)` : `Input ${n}: ${label}`);
      b.setAttribute("aria-pressed", String(n === state.active));
      b.disabled = beyond || !state.online || state.busy || !!state.peek;
      if (!beyond) {
        b.onclick = () => selectInput(n);
        b.oncontextmenu = (e) => {
          e.preventDefault();
          renameInput(n);
        };
      }
      grid.append(b);
    }
    document.body.classList.toggle("preview-16", displayCount() >= 16);
    layoutInputGrid();

    const nameRoot = $("#name");
    const nameTrack = $("#name-track") || nameRoot;
    const activeName =
      state.active == null
        ? "—"
        : `${String(state.active).padStart(2, "0")}  ${state.names[String(state.active)] || state.names[state.active] || `INPUT ${state.active}`}`;
    const banner = liveBanner();
    const showing = Boolean(banner && banner.showing);
    const bootText = state.bootPhase ? String(state.bootPhase) : "";
    const showBoot = Boolean(bootText) && !showing;
    const headline = showing
      ? banner.text
      : showBoot
        ? bootText
        : !state.online
          ? "CONTROLLER OFFLINE"
          : activeName;
    nameRoot.classList.toggle("is-banner", showing || showBoot);
    nameRoot.classList.toggle("is-boot", showBoot);
    setNameTrack(nameRoot, nameTrack, showing ? banner.text : headline, showing || showBoot);
    syncMessageButton(banner);
    const peekSecs = Math.max(1, Number(state.peekSeconds) || Number($("#seconds").value) || 5);
    const peekLabel = `PEEK ${String(state.mode || "off").toUpperCase()} (${peekSecs} SEC)`;
    $("#status").textContent = state.peek
      ? `PEEK • RETURNING TO ${String(state.peek.origin).padStart(2, "0")}`
      : showing
        ? "MESSAGE"
        : showBoot
          ? "STARTING"
          : state.online
            ? `INPUT ${state.active} SELECTED`
            : "INPUT STATE UNKNOWN";
    let detail = `${state.message} • ${peekLabel}`;
    if (state.peek) {
      detail = `TEMPORARY INPUT • ${peekLabel}`;
    } else if (showing) {
      detail =
        banner.mode === "sticky"
          ? "LCD MESSAGE • UNTIL DISMISSED"
          : `LCD MESSAGE • ${Math.max(1, Math.ceil(banner.remaining || 0))} SEC`;
    } else if (showBoot) {
      detail = bootText;
    }
    $("#detail").textContent = detail;

    $$(".peek-mode-btn").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode));
      b.disabled = state.busy || !!state.peek || !state.online;
    });
    const lcdPeek = $("#lcd-peek-mode");
    if (lcdPeek) {
      const modeName = String(state.mode || "off").toUpperCase();
      const peekLocked = state.busy || !!state.peek || !state.online;
      lcdPeek.disabled = peekLocked;
      lcdPeek.dataset.mode = state.mode || "off";
      lcdPeek.setAttribute(
        "aria-label",
        `Peek mode: ${modeName}. Click to cycle Off, Once, Always.`
      );
      lcdPeek.title = peekLocked
        ? `Peek ${modeName}`
        : `Peek ${modeName} — click to cycle Off → Once → Always`;
    }
    $("#seconds").disabled = state.busy || !!state.peek;
    $("#prev").disabled = $("#next").disabled = !state.online || state.busy || !!state.peek;
    $("#cycle").disabled = !state.online || state.busy;
    $("#digits").textContent = state.peek
      ? String(Math.max(0, Math.ceil((state.peek.end - performance.now()) / 1000))).padStart(2, "0")
      : String(peekSecs).padStart(2, "0");
    $("#endpoint").textContent = state.endpoint || "";

    if (state.busy) setLed("busy");
    else if (!state.online) setLed("offline");
    else setLed("connected");
  }

  async function refresh() {
    const wasOnline = state.online;
    try {
      const data = await api("GET", "/api/state?network=0");
      state.online = true;
      state.active = data.active_input ?? data.activeInput ?? state.active;
      state.realCount = data.input_count ?? data.inputCount ?? state.realCount;
      state.count = displayCount();
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
      } else {
        state.cycleList = [];
      }
      updateCycleInputsSummary();
      if (typeof rotate.seconds === "number") {
        $("#cycle-seconds").value = String(rotate.seconds);
      }
      if (!state.peek) state.message = "Ready";
      applyBannerFromState(data.banner, data.banner_history);
      draw();
    } catch (err) {
      state.online = false;
      state.message = err.message || "Offline";
      setLed("error");
      draw();
    }
    if (wasOnline !== state.online) schedulePoll();
  }

  function applyPushEvent(data) {
    if (!data || !data.event) return;
    const kind = String(data.event);
    if (kind === "hello") {
      state.sse = true;
      schedulePoll();
      return;
    }
    if (kind === "input_changed") {
      if (typeof data.active_input === "number") state.active = data.active_input;
      if (data.names && typeof data.names === "object") state.names = data.names;
      // Unsolicited input reports only arrive when the switch is reachable.
      const wasOnline = state.online;
      state.online = true;
      if (!state.peek) state.message = "Ready";
      draw();
      if (!wasOnline) schedulePoll();
      return;
    }
    if (kind === "names_changed") {
      if (data.names && typeof data.names === "object") state.names = data.names;
      draw();
      return;
    }
    if (kind === "banner") {
      applyBannerFromState(data.banner, data.banner_history);
      draw();
    }
  }

  function schedulePoll() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    const ms = !state.online
      ? POLL_OFFLINE_MS
      : state.sse
        ? POLL_SSE_MS
        : POLL_MS;
    state.pollTimer = setInterval(() => {
      if (!state.busy && !state.peek) refresh();
    }, ms);
  }

  function startEvents() {
    if (typeof EventSource === "undefined") return;
    if (state.eventSource) {
      try {
        state.eventSource.close();
      } catch (_) {
        /* ignore */
      }
      state.eventSource = null;
    }
    const es = new EventSource("/api/events");
    state.eventSource = es;
    const onNamed = (ev) => {
      try {
        applyPushEvent(JSON.parse(ev.data));
      } catch (_) {
        /* ignore malformed frames */
      }
    };
    ["hello", "input_changed", "names_changed", "banner"].forEach((name) => {
      es.addEventListener(name, onNamed);
    });
    es.onerror = () => {
      if (state.sse) {
        state.sse = false;
        schedulePoll();
      }
    };
  }

  function liveBanner() {
    const banner = state.banner;
    if (!banner || !banner.text) return null;
    if (!banner.showing) return { ...banner, remaining: null };
    if (banner.mode === "sticky") return { ...banner, remaining: null };
    const remaining = (banner.untilMs - performance.now()) / 1000;
    if (remaining <= 0) {
      banner.showing = false;
      banner.remaining = null;
      banner.untilMs = 0;
      return { ...banner };
    }
    return { ...banner, remaining };
  }

  function applyBannerFromState(raw, history) {
    state.bannerHistory = Array.isArray(history)
      ? history.map((item) => ({
          id: item.id,
          text: String(item.text || ""),
          at: item.at,
          at_iso: item.at_iso || null,
          mode: item.mode === "sticky" ? "sticky" : "timed",
          seconds: item.seconds == null ? null : Number(item.seconds),
        }))
      : [];

    if (!raw || !raw.text) {
      state.lastBroadcastId = null;
      state.lastServerShowing = null;
      // Clear erases history; drop any local display too.
      if (!state.bannerHistory.length) state.banner = null;
      else if (state.banner && !state.banner.fromHistory) state.banner = null;
      return;
    }

    const mode = raw.mode === "sticky" ? "sticky" : "timed";
    const prev = state.banner;
    const serverShowing = Boolean(raw.showing);
    const isNewBroadcast = state.lastBroadcastId !== raw.id;
    const serverReshow =
      !isNewBroadcast && state.lastServerShowing === false && serverShowing;
    state.lastBroadcastId = raw.id;
    state.lastServerShowing = serverShowing;

    function timedUntilMs() {
      if (!serverShowing || mode !== "timed") return 0;
      const remaining = Number(raw.remaining);
      const seconds = Number.isFinite(remaining) && remaining > 0
        ? remaining
        : Number(raw.seconds) || 8;
      return performance.now() + seconds * 1000;
    }

    // A newly sent message always takes the LCD on this kit.
    if (isNewBroadcast) {
      const showing = serverShowing;
      const untilMs = timedUntilMs();
      state.banner = {
        id: raw.id,
        text: String(raw.text),
        mode,
        seconds: raw.seconds == null ? null : Number(raw.seconds),
        at: raw.at,
        at_iso: raw.at_iso || null,
        showing,
        remaining: null,
        untilMs,
        fromHistory: false,
      };
      if (showing && mode === "timed") scheduleBannerTick();
      return;
    }

    // Same broadcast id: history picks stay local-only on this kit.
    if (prev && prev.fromHistory) {
      if (prev.showing && prev.mode === "timed" && prev.untilMs && prev.untilMs <= performance.now()) {
        prev.showing = false;
        prev.untilMs = 0;
      }
      return;
    }

    // Server dismiss / clear-show: Hubitat dismissBanner, CLI --dismiss, panel Dismiss.
    // Server reshow: Hubitat reshowBanner / CLI --show. Otherwise keep this kit's local toggle.
    let showing;
    let untilMs = (prev && prev.untilMs) || 0;
    if (!serverShowing) {
      showing = false;
      untilMs = 0;
    } else if (serverReshow) {
      showing = true;
      untilMs = timedUntilMs();
    } else {
      showing = Boolean(prev && prev.showing);
      if (showing && mode === "timed" && untilMs && untilMs <= performance.now()) {
        showing = false;
        untilMs = 0;
      }
    }
    state.banner = {
      id: raw.id,
      text: String(raw.text),
      mode,
      seconds: raw.seconds == null ? null : Number(raw.seconds),
      at: raw.at,
      at_iso: raw.at_iso || null,
      showing,
      remaining: null,
      untilMs,
      fromHistory: false,
    };
    if (showing && mode === "timed") scheduleBannerTick();
  }

  function formatBannerWhen(at, atIso) {
    const ms = Number(at) * 1000;
    if (Number.isFinite(ms) && ms > 0) {
      try {
        return new Date(ms).toLocaleString(undefined, {
          year: "numeric",
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
      } catch {
        /* fall through */
      }
    }
    return atIso || "";
  }

  function syncMessageButton(banner) {
    const btn = $("#btn-message");
    const listBtn = $("#btn-message-list");
    const hasLast = Boolean(banner && banner.text);
    const hasHistory = state.bannerHistory.length > 0;
    if (btn) {
      btn.hidden = !hasLast;
      btn.classList.toggle("is-active", Boolean(hasLast && banner.showing));
      btn.title = banner && banner.showing
        ? "Hide message on this kit"
        : "Show last message on this kit";
      btn.setAttribute("aria-label", btn.title);
    }
    if (listBtn) {
      listBtn.hidden = !hasHistory;
      listBtn.title = "Browse last messages on this kit";
      listBtn.setAttribute("aria-label", listBtn.title);
    }
  }

  function showBannerLocal() {
    const banner = state.banner;
    if (!banner || !banner.text) return;
    banner.showing = true;
    if (banner.mode === "timed") {
      const seconds = Number(banner.seconds) || 8;
      banner.untilMs = performance.now() + seconds * 1000;
      scheduleBannerTick();
    } else {
      banner.untilMs = 0;
    }
  }

  function hideBannerLocal() {
    if (!state.banner) return;
    state.banner.showing = false;
    state.banner.untilMs = 0;
    state.banner.remaining = null;
  }

  function toggleLastMessage() {
    if (!state.banner || !state.banner.text) return;
    if (state.banner.showing) hideBannerLocal();
    else showBannerLocal();
    state.message = state.banner.showing ? "Message shown" : "Message hidden";
    draw();
  }

  function dismissBanner() {
    hideBannerLocal();
    state.message = "Message dismissed";
    draw();
  }

  function showHistoryEntryLocal(entry) {
    if (!entry || !entry.text) return;
    const mode = entry.mode === "sticky" ? "sticky" : "timed";
    state.banner = {
      id: entry.id,
      text: String(entry.text),
      mode,
      seconds: entry.seconds == null ? null : Number(entry.seconds),
      at: entry.at,
      at_iso: entry.at_iso || null,
      showing: true,
      remaining: null,
      untilMs: 0,
      fromHistory: true,
    };
    if (mode === "timed") {
      const seconds = Number(entry.seconds) || 8;
      state.banner.untilMs = performance.now() + seconds * 1000;
      scheduleBannerTick();
    }
    state.message = "History message shown";
    draw();
  }

  // Mini (and other app shells) cannot paint outside the OS window — expand
  // briefly so the history list is readable, then snap back on close.
  const HISTORY_POPOUT = { width: 460, height: 620 };
  let historyPopoutOpen = false;

  async function resizeKitWindow(width, height) {
    const apiBridge = typeof pyApi === "function" ? pyApi() : (window.pywebview && window.pywebview.api) || null;
    ignoreResize = true;
    try {
      if (apiBridge && apiBridge.resize_to) {
        await Promise.resolve(apiBridge.resize_to(width, height));
      } else if (isApp || isPopup) {
        try {
          window.resizeTo(width, height);
        } catch {
          /* browser may block resizeTo */
        }
      }
      if (typeof waitForWindowSize === "function") {
        await waitForWindowSize(width, height);
      }
    } catch {
      /* ignore */
    }
    ignoreResize = false;
  }

  async function expandForMessageHistory() {
    if (historyPopoutOpen || (!isApp && !isPopup)) return;
    historyPopoutOpen = true;
    document.body.classList.add("is-history-popout");
    await resizeKitWindow(HISTORY_POPOUT.width, HISTORY_POPOUT.height);
  }

  async function restoreAfterMessageHistory() {
    if (!historyPopoutOpen) return;
    historyPopoutOpen = false;
    document.body.classList.remove("is-history-popout");
    // Snap back to the current view mode size (Mini / Compact / Full).
    if (typeof sizeWidgetToMode === "function") {
      sizeWidgetToMode({ resizeWindow: true });
    }
  }

  function closeMessageHistoryDialog() {
    const dialog = $("#message-history-dialog");
    if (dialog) dialog.hidden = true;
    restoreAfterMessageHistory();
  }

  async function openMessageHistoryDialog() {
    const dialog = $("#message-history-dialog");
    const list = $("#message-history-list");
    if (!dialog || !list) return;
    await expandForMessageHistory();
    list.replaceChildren();
    if (!state.bannerHistory.length) {
      const empty = document.createElement("p");
      empty.className = "kit-dialog-hint";
      empty.textContent = "No messages yet.";
      list.append(empty);
    } else {
      state.bannerHistory.forEach((entry) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "message-history-item";
        btn.setAttribute("role", "option");
        const active = state.banner && state.banner.showing && state.banner.id === entry.id;
        btn.setAttribute("aria-selected", String(Boolean(active)));
        if (active) btn.classList.add("is-active");

        const text = document.createElement("span");
        text.className = "message-history-text";
        text.textContent = entry.text;

        const meta = document.createElement("span");
        meta.className = "message-history-meta";
        const when = formatBannerWhen(entry.at, entry.at_iso);
        const kind =
          entry.mode === "sticky"
            ? "Sticky"
            : `${Number(entry.seconds) || 8}s timed`;
        meta.textContent = when ? `${when} · ${kind}` : kind;

        btn.append(text, meta);
        btn.onclick = () => {
          showHistoryEntryLocal(entry);
          closeMessageHistoryDialog();
        };
        list.append(btn);
      });
    }
    dialog.hidden = false;
  }

  function scheduleBannerTick() {
    if (state.bannerTimer) clearTimeout(state.bannerTimer);
    state.bannerTimer = setTimeout(() => {
      state.bannerTimer = 0;
      const banner = liveBanner();
      draw();
      if (banner && banner.showing && banner.mode === "timed") scheduleBannerTick();
    }, 250);
  }

  function makeMessageUnit(text, { clone = false } = {}) {
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

  function setBootPhase(text, { sticky = false } = {}) {
    if (state.bootClearTimer) {
      clearTimeout(state.bootClearTimer);
      state.bootClearTimer = 0;
    }
    state.bootPhase = text ? String(text) : null;
    if (text) state.message = String(text);
    draw();
    if (text && !sticky) {
      // Real banners / online state replace this; otherwise auto-clear after a beat.
      state.bootClearTimer = setTimeout(() => {
        state.bootClearTimer = 0;
        if (state.bootPhase === text) {
          state.bootPhase = null;
          draw();
        }
      }, 1400);
    }
  }

  function clearBootPhase() {
    if (state.bootClearTimer) {
      clearTimeout(state.bootClearTimer);
      state.bootClearTimer = 0;
    }
    if (!state.bootPhase) return;
    state.bootPhase = null;
    draw();
  }

  function setNameTrack(nameRoot, track, text, isBanner) {
    if (!nameRoot || !track) return;
    const isBoot = nameRoot.classList.contains("is-boot");
    const key = `${isBanner ? (isBoot ? "boot" : "b") : "n"}:${text}`;
    if (track.dataset.key === key) {
      // Keep a running marquee alive across timed redraws.
      if (isBanner && !nameRoot.classList.contains("is-scrolling")) {
        requestAnimationFrame(() => syncNameScroll(nameRoot, track));
      }
      return;
    }
    track.dataset.key = key;
    track.replaceChildren();
    if (isBanner) {
      track.append(makeMessageUnit(text), makeMessageUnit(text, { clone: true }));
    } else {
      const plain = document.createElement("span");
      plain.className = "name-plain";
      plain.textContent = text;
      track.append(plain);
    }
    requestAnimationFrame(() => syncNameScroll(nameRoot, track));
  }

  function syncNameScroll(nameRoot, track) {
    if (!nameRoot || !track) return;
    const isBanner = nameRoot.classList.contains("is-banner");
    const isBoot = nameRoot.classList.contains("is-boot");
    const unit = track.querySelector(".name-unit");
    if (!isBanner || !unit) {
      nameRoot.classList.remove("is-scrolling");
      track.style.removeProperty("--lcd-scroll-distance");
      track.style.removeProperty("--lcd-scroll-duration");
      const units = track.querySelectorAll(".name-unit");
      if (units.length > 1) units[1].remove();
      return;
    }
    // Measure content without the loop gap / duplicate.
    nameRoot.classList.remove("is-scrolling");
    const extras = [...track.querySelectorAll(".name-unit")].slice(1);
    extras.forEach((el) => {
      el.style.display = "none";
    });
    const contentWidth = unit.getBoundingClientRect().width;
    extras.forEach((el) => {
      el.style.display = "";
    });
    // Boot steps are often short — always marquee so the LCD feels active.
    const needsScroll = isBoot || contentWidth > nameRoot.clientWidth + 2;
    if (!needsScroll) {
      track.style.removeProperty("--lcd-scroll-distance");
      track.style.removeProperty("--lcd-scroll-duration");
      extras.forEach((el) => el.remove());
      return;
    }
    if (track.querySelectorAll(".name-unit").length < 2) {
      track.append(makeMessageUnit(unit.querySelector(".name-unit-text")?.textContent || "", { clone: true }));
    }
    nameRoot.classList.add("is-scrolling");
    // Segment = icon + text + trailing gap between repeats.
    const segment = unit.getBoundingClientRect().width;
    const seconds = Math.max(isBoot ? 4 : 6, Math.min(28, segment / 32));
    track.style.setProperty("--lcd-scroll-distance", `${segment}px`);
    track.style.setProperty("--lcd-scroll-duration", `${seconds}s`);
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
    if (!state.online || state.busy || state.peek) return;
    if (n === state.active) {
      const banner = liveBanner();
      if (banner && banner.showing && banner.mode === "sticky") {
        await dismissBanner();
      }
      return;
    }
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

  const PEEK_FOLLOW_MODES = ["off", "once", "always"];

  function nextPeekFollowMode(current) {
    const i = PEEK_FOLLOW_MODES.indexOf(current);
    return PEEK_FOLLOW_MODES[((i < 0 ? 0 : i) + 1) % PEEK_FOLLOW_MODES.length];
  }

  async function setPeekMode(mode) {
    await withBusy(async () => {
      const seconds = Math.max(1, Math.min(60, Number($("#seconds").value) || 5));
      if (mode === "off") await api("POST", "/api/peek-follow", { mode: "off" });
      else await api("POST", "/api/peek-follow", { mode, seconds });
      state.mode = mode;
      state.peekSeconds = seconds;
      state.message = `Peek ${mode}`;
      // "once" is one-shot, so only the lasting choices are remembered.
      if (mode !== "once") prefs.save({ peek_follow: mode, peek_seconds: seconds });
      await refresh();
    });
  }

  async function cyclePeekFollowMode() {
    if (state.busy || state.peek || !state.online) return;
    await setPeekMode(nextPeekFollowMode(state.mode || "off"));
  }

  // Re-arm a remembered "always" peek-follow when the listener has it off
  // (listener restarts forget it). Never pushes "off" over another client.
  async function restorePeekFollow() {
    if (prefs.get("peek_follow") !== "always") return;
    for (let i = 0; i < 20 && !state.online; i++) {
      await new Promise((r) => setTimeout(r, 500));
    }
    if (!state.online || state.mode !== "off") return;
    const seconds = Number(prefs.get("peek_seconds")) || state.peekSeconds;
    await withBusy(async () => {
      await api("POST", "/api/peek-follow", { mode: "always", seconds });
      state.mode = "always";
      state.message = "Peek always (remembered)";
      await refresh();
    });
  }

  function cycleStep(direction = 1) {
    const list =
      state.cycleList.length > 0
        ? state.cycleList
        : Array.from({ length: state.realCount || state.count }, (_, i) => i + 1);
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

  $$(".peek-mode-btn").forEach((b) => {
    b.onclick = () => setPeekMode(b.dataset.mode);
  });

  const secondsInput = $("#seconds");
  if (secondsInput) {
    secondsInput.onchange = async () => {
      const seconds = Math.max(1, Math.min(60, Number(secondsInput.value) || 5));
      secondsInput.value = String(seconds);
      await withBusy(async () => {
        await api("POST", "/api/peek-default", { seconds });
        state.peekSeconds = seconds;
        state.message = `Peek default ${seconds}s`;
      });
    };
  }

  const bindClick = (sel, fn) => {
    const el = $(sel);
    if (el) el.onclick = fn;
  };
  bindClick("#lcd-peek-mode", () => cyclePeekFollowMode());
  bindClick("#prev", () => selectInput(cycleStep(-1)));
  bindClick("#next", () => selectInput(cycleStep(1)));
  bindClick("#mini-prev", () => selectInput(cycleStep(-1)));
  bindClick("#mini-next", () => selectInput(cycleStep(1)));
  bindClick("#btn-message", () => toggleLastMessage());
  bindClick("#btn-message-list", () => openMessageHistoryDialog());
  bindClick("#message-history-close", () => closeMessageHistoryDialog());
  const messageHistoryDialog = $("#message-history-dialog");
  if (messageHistoryDialog) {
    messageHistoryDialog.onclick = (event) => {
      if (event.target === messageHistoryDialog) closeMessageHistoryDialog();
    };
  }

  // Opens in Compact, then switches to the remembered size once prefs load.
  // Click cycles Compact → Mini → Full → Compact; the label is the *current* mode.
  const VIEW_MODES = ["compact", "mini", "full"];
  const VIEW_DEFAULT = "compact";

  function viewMode() {
    const deck = $("#deck");
    if (!deck) return VIEW_DEFAULT;
    if (deck.classList.contains("mini")) return "mini";
    if (deck.classList.contains("compact")) return "compact";
    return "full";
  }

  function viewModeTitle(mode) {
    if (mode === "mini") return "Mini";
    if (mode === "full") return "Full";
    return "Compact";
  }

  function setViewMode(mode, { resizeWindow = true, remember = false } = {}) {
    const deck = $("#deck");
    const btn = $("#view-mode");
    const label = $("#view-mode-label");
    const miniNav = $("#mini-nav");
    if (!deck) return;
    const next = VIEW_MODES.includes(mode) ? mode : VIEW_DEFAULT;
    deck.classList.remove("compact", "mini");
    if (next === "compact") deck.classList.add("compact");
    if (next === "mini") deck.classList.add("mini");
    deck.dataset.mode = next;
    if (miniNav) miniNav.hidden = next !== "mini";
    if (btn) {
      // Cycle control, not a toggle — do not leave aria-pressed on Compact/Mini
      // (that was painting the cyan "selected" border permanently).
      btn.removeAttribute("aria-pressed");
      btn.dataset.mode = next;
      btn.title = "Click to cycle Compact → Mini → Full";
      btn.setAttribute("aria-label", `View: ${viewModeTitle(next)}. Click to change size.`);
    }
    if (label) label.textContent = viewModeTitle(next);
    if (remember) prefs.save({ view: next });
    // Always collapse Setup when changing size so Compact/Mini/Full stay tidy.
    if (typeof setSetupDoor === "function") setSetupDoor(false);
    try {
      draw();
    } catch (err) {
      console.error("kit draw after view change failed", err);
    }
    try {
      if (typeof sizeWidgetToMode === "function") sizeWidgetToMode({ resizeWindow });
    } catch (err) {
      console.error("kit resize after view change failed", err);
    }
  }

  setViewMode(VIEW_DEFAULT, { resizeWindow: false });
  bindClick("#view-mode", (e) => {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    const cur = viewMode();
    const idx = VIEW_MODES.indexOf(cur);
    const next = VIEW_MODES[(idx < 0 ? 0 : idx + 1) % VIEW_MODES.length];
    setViewMode(next, { resizeWindow: true, remember: true });
  });

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
    // Mini has no setup bay — keep the door closed even if it was open before.
    setSetupDoor(viewMode() === "mini" ? false : open);
    bindClick("#service-door", () => {
      if (viewMode() === "mini") return;
      const next = $("#service-bay").dataset.open !== "true";
      setSetupDoor(next);
      sizeWidgetToMode({ resizeWindow: true });
    });
  })();

  bindClick("#cycle", async () => {
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
  });

  function cycleSummaryText(list) {
    const n = state.realCount || state.count;
    if (!list.length) return "All";
    if (list.length === n) return "All";
    return list.join(", ");
  }

  /** Normalize a picker selection for the Inputs button / saved cycle. */
  function cycleListFromSelection(selected, ports) {
    const list = [...selected].map(Number).filter((n) => n >= 1 && n <= ports).sort((a, b) => a - b);
    // Empty or every port means "all inputs" (same as the listener).
    if (!list.length || list.length === ports) return [];
    return list;
  }

  function updateCycleInputsSummary(list = state.cycleList) {
    const el = $("#cycle-inputs-summary");
    if (el) el.textContent = cycleSummaryText(list);
  }

  function openCyclePicker() {
    const dialog = $("#cycle-dialog");
    const picker = $("#cycle-picker");
    if (!dialog || !picker) return Promise.resolve(null);

    const ports = state.realCount || state.count;
    const selected = new Set(
      state.cycleList.length ? state.cycleList : Array.from({ length: ports }, (_, i) => i + 1),
    );

    const previewSummary = () => {
      const el = $("#cycle-inputs-summary");
      if (!el) return;
      // Show the tiles as they stand (not the saved “empty means all” rule).
      if (selected.size === 0) {
        el.textContent = "None";
        return;
      }
      if (selected.size === ports) {
        el.textContent = "All";
        return;
      }
      el.textContent = [...selected]
        .map(Number)
        .sort((a, b) => a - b)
        .join(", ");
    };

    const render = () => {
      picker.replaceChildren();
      for (let n = 1; n <= ports; n++) {
        const on = selected.has(n);
        const b = document.createElement("button");
        b.type = "button";
        b.setAttribute("aria-pressed", String(on));
        b.setAttribute("aria-label", on ? `Input ${n} selected` : `Input ${n} not selected`);
        const name = state.names[String(n)] || state.names[n] || `Input ${n}`;
        b.innerHTML = `<span>${n}</span><span class="pick-name"></span>`;
        b.querySelector(".pick-name").textContent = name;
        b.onclick = () => {
          if (selected.has(n)) selected.delete(n);
          else selected.add(n);
          const nowOn = selected.has(n);
          b.setAttribute("aria-pressed", String(nowOn));
          b.setAttribute("aria-label", nowOn ? `Input ${n} selected` : `Input ${n} not selected`);
          previewSummary();
        };
        picker.append(b);
      }
      previewSummary();
    };
    render();
    dialog.hidden = false;

    return new Promise((resolve) => {
      const finish = (value) => {
        dialog.hidden = true;
        dialog.removeEventListener("keydown", onKey);
        $("#cycle-pick-save").onclick = null;
        $("#cycle-pick-cancel").onclick = null;
        $("#cycle-pick-all").onclick = null;
        $("#cycle-pick-none").onclick = null;
        dialog.onclick = null;
        // Cancel / backdrop: put the Inputs label back to the saved list.
        if (value === null) updateCycleInputsSummary();
        resolve(value);
      };
      const onKey = (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          finish(null);
        } else if (e.key === "Enter") {
          e.preventDefault();
          finish(cycleListFromSelection(selected, ports));
        }
      };
      dialog.addEventListener("keydown", onKey);
      $("#cycle-pick-all").onclick = () => {
        for (let n = 1; n <= ports; n++) selected.add(n);
        render();
      };
      $("#cycle-pick-none").onclick = () => {
        selected.clear();
        render();
      };
      $("#cycle-pick-cancel").onclick = () => finish(null);
      $("#cycle-pick-save").onclick = () => finish(cycleListFromSelection(selected, ports));
      dialog.onclick = (e) => {
        if (e.target === dialog) finish(null);
      };
    });
  }

  bindClick("#cycle-inputs-btn", async () => {
    const picked = await openCyclePicker();
    if (picked === null) return;
    // picked is already normalized ([] = all inputs).
    state.cycleList = picked;
    const err = $("#cycle-error");
    if (err) err.textContent = "";
    updateCycleInputsSummary();
    stopCycle();
    await withBusy(async () => {
      await saveCycleConfig();
      state.message = state.cycleList.length
        ? `Cycle: ${state.cycleList.join(", ")}`
        : "Cycle uses all inputs";
      await refresh();
    });
  });

  const cycleSeconds = $("#cycle-seconds");
  if (cycleSeconds) {
    cycleSeconds.onchange = () => {
      stopCycle();
      withBusy(async () => {
        await saveCycleConfig();
        state.message = "Cycle interval saved";
      });
    };
  }

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

  function openBrowserPopup() {
    const url = `${location.origin}/kit?popup=1`;
    const features = "popup=yes,width=380,height=356,resizable=yes";
    const win = window.open(url, "tesmart-kit", features);
    if (win) {
      try {
        win.focus();
      } catch {
        /* ignore */
      }
    }
  }

  bindClick("#btn-popout", async () => {
    // Frameless pywebview (preferred) or Chromium --app on the listener host.
    try {
      await api("POST", "/api/kit-window", { prefer: "auto" });
      state.message = "Opened frameless window on the listener host";
      draw();
    } catch {
      openBrowserPopup();
    }
  });

  function pyApi() {
    return (window.pywebview && window.pywebview.api) || null;
  }

  const powerBtn = $("#btn-power") || $("#btn-close");
  if (powerBtn) {
    powerBtn.addEventListener("click", (e) => {
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

  // View modes snap the OS window to a design size (clamped to *this*
  // display — important on portrait / secondary monitors). Manual resize
  // lets the deck fill the window; buttons stay fixed-width.
  let ignoreResize = false;
  let resizeTimer = null;
  let sizeToken = 0;

  async function screenAvail() {
    const apiBridge = pyApi();
    if (apiBridge && apiBridge.screen_size) {
      try {
        const info = await Promise.resolve(apiBridge.screen_size());
        if (info && info.width && info.height) {
          return {
            w: Math.max(280, Number(info.width) - 8),
            h: Math.max(160, Number(info.height) - 8),
          };
        }
      } catch {
        /* fall through */
      }
    }
    return {
      w: Math.max(280, (window.screen && window.screen.availWidth) || window.innerWidth || 1200),
      h: Math.max(160, (window.screen && window.screen.availHeight) || window.innerHeight || 800),
    };
  }

  function designWidthFor(mode, availW) {
    const ideal = mode === "mini" ? 340 : mode === "compact" ? 380 : 720;
    return Math.max(280, Math.min(ideal, availW));
  }

  function layoutInputGrid() {
    const grid = $("#grid");
    const deck = $("#deck");
    if (!grid || !deck || deck.classList.contains("mini")) return;
    const count = Math.max(1, displayCount() || grid.children.length || 8);
    const cs = getComputedStyle(deck);
    const btn = parseFloat(cs.getPropertyValue("--kit-btn")) || 156;
    const gap = parseFloat(cs.getPropertyValue("--kit-gap")) || 12;
    // Prefer the grid's own width; fall back to content box of the deck.
    const avail =
      Math.floor(grid.getBoundingClientRect().width) ||
      Math.max(0, Math.floor(deck.clientWidth) - 8);
    const fourFixed = 4 * btn + 3 * gap;
    const oneRow = count * btn + Math.max(0, count - 1) * gap;

    // Narrow / portrait: fluid 4-col so keys stay inside the rounded panel
    // (fixed 156px tracks were clipped by border-radius overflow).
    if (avail < fourFixed - 1) {
      grid.style.gridTemplateColumns = `repeat(${Math.min(4, count)}, minmax(0, 1fr))`;
      grid.dataset.layout = "fluid";
      grid.dataset.cols = String(Math.min(4, count));
      return;
    }
    const cols = avail >= oneRow - 1 ? count : Math.min(4, count);
    grid.style.gridTemplateColumns = `repeat(${cols}, ${btn}px)`;
    grid.dataset.layout = "fixed";
    grid.dataset.cols = String(cols);
  }

  function fillDeckWidth() {
    const deck = $("#deck");
    if (!deck) return;
    deck.style.zoom = "1";
    deck.style.width = "100%";
    deck.style.maxWidth = "none";
    layoutInputGrid();
  }

  function measureDeckAt(width) {
    const deck = $("#deck");
    deck.style.zoom = "1";
    deck.style.width = `${width}px`;
    deck.style.maxWidth = "none";
    // Drop the fill-to-window rules for this measure. height:100% /
    // min-height:100% would otherwise report the *window* height and the
    // snap would never shrink (tall empty band under Setup on --fresh).
    deck.style.height = "auto";
    deck.style.minHeight = "0";
    // Column layout depends on width — without this, Full→Compact keeps a
    // one-row grid, under-measures height, and clips Setup.
    layoutInputGrid();
    void deck.offsetHeight;

    // Mini lays LCD and Up/Down side-by-side — summing children would count
    // both column heights and make the window ~100px too tall.
    let height;
    if (viewMode() === "mini") {
      height = Math.ceil(Math.max(deck.scrollHeight, deck.getBoundingClientRect().height));
    } else {
      let contentH = 0;
      for (const child of deck.children) {
        if (child.hidden) continue;
        const cs = getComputedStyle(child);
        if (cs.display === "none") continue;
        contentH += child.getBoundingClientRect().height;
        contentH += parseFloat(cs.marginTop) || 0;
        contentH += parseFloat(cs.marginBottom) || 0;
      }
      const style = getComputedStyle(deck);
      const chrome =
        (parseFloat(style.paddingTop) || 0) +
        (parseFloat(style.paddingBottom) || 0) +
        (parseFloat(style.borderTopWidth) || 0) +
        (parseFloat(style.borderBottomWidth) || 0);
      height = Math.ceil(
        Math.max(contentH + chrome, deck.scrollHeight, deck.getBoundingClientRect().height),
      );
    }
    // Restore fill-to-window so a later user drag still stretches the device.
    deck.style.height = "";
    deck.style.minHeight = "";
    return { width, height: Math.max(120, height) };
  }

  async function measureDeck() {
    const avail = await screenAvail();
    const width = designWidthFor(viewMode(), avail.w);
    const measured = measureDeckAt(width);
    return {
      width: measured.width,
      height: Math.min(measured.height, avail.h),
    };
  }

  function applyDeckLayout() {
    if (!isApp && !isPopup) return;
    fillDeckWidth();
  }

  function waitForWindowSize(width, height, attempts = 12) {
    return new Promise((resolve) => {
      const tick = (left) => {
        // Portrait / DPI: tolerate a looser match so we do not thrash resize.
        if (
          Math.abs(window.innerWidth - width) <= 24 &&
          Math.abs(window.innerHeight - height) <= 24
        ) {
          resolve(true);
          return;
        }
        if (left <= 0) {
          resolve(false);
          return;
        }
        setTimeout(() => tick(left - 1), 40);
      };
      tick(attempts);
    });
  }

  // Resize floors: Full ≥ Compact snap, Compact ≥ Mini snap, Mini ≥ Mini snap.
  // Cached whenever a mode is measured so we do not have to flash another layout.
  const modeSnap = { mini: null, compact: null, full: null };
  let minWindow = null;

  function resizeFloorMode(mode) {
    return mode === "full" ? "compact" : "mini";
  }

  function withTempViewMode(mode, fn) {
    const deck = $("#deck");
    const miniNav = $("#mini-nav");
    if (!deck) return fn();
    const prev = {
      compact: deck.classList.contains("compact"),
      mini: deck.classList.contains("mini"),
      mode: deck.dataset.mode,
      navHidden: miniNav ? miniNav.hidden : true,
    };
    deck.classList.remove("compact", "mini");
    if (mode === "compact") deck.classList.add("compact");
    if (mode === "mini") deck.classList.add("mini");
    deck.dataset.mode = mode;
    if (miniNav) miniNav.hidden = mode !== "mini";
    try {
      return fn();
    } finally {
      deck.classList.toggle("compact", prev.compact);
      deck.classList.toggle("mini", prev.mini);
      deck.dataset.mode = prev.mode;
      if (miniNav) miniNav.hidden = prev.navHidden;
    }
  }

  async function resizeFloorSize(mode) {
    const floor = resizeFloorMode(mode);
    if (modeSnap[floor]) return modeSnap[floor];
    const avail = await screenAvail();
    const width = designWidthFor(floor, avail.w);
    const measured = withTempViewMode(floor, () => measureDeckAt(width));
    modeSnap[floor] = measured;
    return measured;
  }

  async function setMinWindowSize(apiBridge, size) {
    minWindow = size ? { width: size.width, height: size.height } : null;
    if (!apiBridge || !apiBridge.set_min_size) return;
    try {
      const w = minWindow ? minWindow.width : 280;
      const h = minWindow ? minWindow.height : 160;
      await Promise.resolve(apiBridge.set_min_size(w, h));
    } catch {
      /* older host without set_min_size */
    }
  }

  // Browser popups have no native minimum; push back if dragged too small.
  function enforceMinWindowSize() {
    if (!minWindow || pyApi()) return;
    const w = Math.max(window.outerWidth, minWindow.width + (window.outerWidth - window.innerWidth));
    const h = Math.max(window.outerHeight, minWindow.height + (window.outerHeight - window.innerHeight));
    if (w !== window.outerWidth || h !== window.outerHeight) {
      try {
        window.resizeTo(w, h);
      } catch {
        /* only script-opened popups may resize */
      }
    }
  }

  function sizeWidgetToMode({ resizeWindow = false } = {}) {
    if (!isApp && !isPopup) return;
    const deck = $("#deck");
    if (!deck) return;
    const token = ++sizeToken;

    const applySize = async (apiBridge, width, height) => {
      try {
        if (apiBridge && apiBridge.resize_to) {
          await Promise.resolve(apiBridge.resize_to(width, height));
        } else {
          try {
            window.resizeTo(width, height);
          } catch {
            /* browser may block resizeTo */
          }
        }
      } catch {
        /* ignore */
      }
      await waitForWindowSize(width, height);
    };

    const run = async () => {
      if (token !== sizeToken) return;
      // Let Compact/Mini/Full CSS + grid visibility settle before measuring.
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      if (token !== sizeToken) return;

      if (!resizeWindow) {
        await measureDeck();
        fillDeckWidth();
        return;
      }

      ignoreResize = true;
      const apiBridge = pyApi();
      // Lift the old view's floor so a smaller view (Full → Compact/Mini) can snap down.
      await setMinWindowSize(apiBridge, null);
      const mode = viewMode();
      // Compact/Full need a taller frame to measure their real height after Mini.
      // Never grow when snapping *to* Mini — that left a tall empty device.
      if (mode !== "mini") {
        const roomy = Math.max(window.innerHeight || 0, 640);
        const roomyW = Math.max(window.innerWidth || 0, designWidthFor(mode, 1200));
        if ((window.innerHeight || 0) < roomy - 40) {
          await applySize(apiBridge, roomyW, roomy);
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        }
      }
      if (token !== sizeToken) return;

      const { width, height } = await measureDeck();
      await applySize(apiBridge, width, height);
      if (token !== sizeToken) return;
      // Second pass once the 4×2 grid has settled at the design width.
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      if (token !== sizeToken) return;
      const again = await measureDeck();
      if (Math.abs(again.width - width) > 2 || Math.abs(again.height - height) > 2) {
        await applySize(apiBridge, again.width, again.height);
      }
      if (token !== sizeToken) return;
      modeSnap[mode] = again;
      // Floor is the next-smaller view (Full→Compact, Compact/Mini→Mini).
      await setMinWindowSize(apiBridge, await resizeFloorSize(mode));
      fillDeckWidth();
      ignoreResize = false;
    };

    requestAnimationFrame(() => {
      setTimeout(run, 50);
    });
  }

  window.addEventListener("pywebviewready", () => {
    sizeWidgetToMode({ resizeWindow: true });
  });
  if (isApp || isPopup) {
    window.addEventListener("load", () => sizeWidgetToMode({ resizeWindow: true }));
    window.addEventListener("resize", () => {
      if (ignoreResize) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        enforceMinWindowSize();
        applyDeckLayout();
      }, 50);
    });
  }

  bindClick("#btn-on-top", async () => {
    const btn = $("#btn-on-top");
    if (!btn) return;
    const on = btn.getAttribute("aria-pressed") !== "true";
    btn.setAttribute("aria-pressed", String(on));
    const apiBridge = pyApi();
    if (apiBridge && apiBridge.set_on_top) {
      try {
        await apiBridge.set_on_top(on);
      } catch {
        btn.setAttribute("aria-pressed", String(!on));
      }
    }
  });

  const BODY_THEMES = ["black", "silver"];
  const LCD_THEMES = ["green", "blue", "red", "amber"];
  const LCD_GLASS = ["matrix", "phosphor", "off"];
  const HEX_COLOR = /^#[0-9A-Fa-f]{6}$/;

  function normalizeHighlight(value) {
    if (typeof value !== "string") return null;
    const hex = value.trim();
    return HEX_COLOR.test(hex) ? hex.toLowerCase() : null;
  }

  function themeTargets() {
    return [document.body, $("#deck")].filter(Boolean);
  }

  function lcdDefaultSelect() {
    const deck = $("#deck") || document.body;
    const prev = deck.style.getPropertyValue("--kit-select");
    deck.style.removeProperty("--kit-select");
    const value = getComputedStyle(deck).getPropertyValue("--kit-select").trim();
    if (prev) deck.style.setProperty("--kit-select", prev);
    else deck.style.removeProperty("--kit-select");
    return HEX_COLOR.test(value) ? value.toLowerCase() : "#a8c45c";
  }

  function syncHighlightControls(customHex) {
    const picker = $("#highlight-color");
    const reset = $("#highlight-default");
    const usingCustom = Boolean(customHex);
    if (picker) {
      picker.value = usingCustom ? customHex : lcdDefaultSelect();
      picker.title = usingCustom
        ? `Custom highlight ${customHex}`
        : "Custom highlight colour (currently LCD default)";
    }
    if (reset) {
      reset.disabled = !usingCustom;
      reset.setAttribute("aria-pressed", String(usingCustom));
    }
    document.body.dataset.highlight = usingCustom ? "custom" : "default";
    const deck = $("#deck");
    if (deck) deck.dataset.highlight = usingCustom ? "custom" : "default";
  }

  function applyHighlight(value, { persist = false } = {}) {
    const hex = normalizeHighlight(value);
    for (const el of themeTargets()) {
      if (hex) {
        el.style.setProperty("--kit-select", hex);
        // Silver selected keys use --kit-select-glow for the lit glyph + ring.
        el.style.setProperty("--kit-select-glow", hex);
      } else {
        el.style.removeProperty("--kit-select");
        el.style.removeProperty("--kit-select-glow");
      }
    }
    syncHighlightControls(hex);
    if (persist) prefs.save({ highlight: hex });
  }

  function applyBodyTheme(name) {
    const body = BODY_THEMES.includes(name) ? name : "black";
    document.body.dataset.body = body;
    const deck = $("#deck");
    if (deck) deck.dataset.body = body;
    $$(".theme-swatch[data-body]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.body === body));
    });
    // Refresh picker preview when silver/black changes default select.
    if (document.body.dataset.highlight !== "custom") syncHighlightControls(null);
  }

  function applyLcdTheme(name) {
    const lcd = LCD_THEMES.includes(name) ? name : "green";
    document.body.dataset.lcd = lcd;
    const deck = $("#deck");
    if (deck) deck.dataset.lcd = lcd;
    $$(".theme-swatch[data-lcd]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.lcd === lcd));
    });
    if (document.body.dataset.highlight !== "custom") syncHighlightControls(null);
  }

  function applyLcdGlass(name) {
    const glass = LCD_GLASS.includes(name) ? name : "matrix";
    document.body.dataset.glass = glass;
    const deck = $("#deck");
    if (deck) deck.dataset.glass = glass;
    $$(".glass-mode-btn").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.glass === glass));
    });
  }

  $$(".theme-swatch[data-body]").forEach((btn) => {
    btn.onclick = () => {
      applyBodyTheme(btn.dataset.body);
      prefs.save({ body: document.body.dataset.body });
    };
  });
  $$(".theme-swatch[data-lcd]").forEach((btn) => {
    btn.onclick = () => {
      applyLcdTheme(btn.dataset.lcd);
      prefs.save({ lcd: document.body.dataset.lcd });
    };
  });

  const highlightPicker = $("#highlight-color");
  if (highlightPicker) {
    highlightPicker.addEventListener("input", () => {
      applyHighlight(highlightPicker.value, { persist: true });
    });
  }
  const highlightDefault = $("#highlight-default");
  if (highlightDefault) {
    highlightDefault.onclick = () => {
      applyHighlight(null, { persist: true });
    };
  }

  $$(".glass-mode-btn").forEach((btn) => {
    btn.onclick = () => {
      applyLcdGlass(btn.dataset.glass);
      prefs.save({ lcd_glass: document.body.dataset.glass });
    };
  });

  // Drag the title grip. App/pywebview uses api.drag_by (avoids cocoa
  // multi-monitor jump). Browser popups fall back to window.moveBy.
  (() => {
    const handle = $("#drag-handle") || $("#drag");
    if (!handle) return;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let dragChain = Promise.resolve();
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
      if (!dx && !dy) return;
      const apiBridge = pyApi();
      if (apiBridge && apiBridge.drag_by) {
        dragChain = dragChain
          .then(() => Promise.resolve(apiBridge.drag_by(dx, dy)))
          .catch(() => {});
        return;
      }
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

  try {
    draw();
  } catch (err) {
    console.error("kit draw failed", err);
  }

  // Show the deck immediately, then walk boot steps on the LCD marquee.
  // These strings are UI-only — never posted as banners or stored in prefs.
  (async () => {
    setBootPhase("Loading preferences…", { sticky: true });
    try {
      await prefs.load();
      applyBodyTheme(prefs.get("body") || "black");
      applyLcdTheme(prefs.get("lcd") || "green");
      applyLcdGlass(prefs.get("lcd_glass") || "matrix");
      applyHighlight(prefs.get("highlight"));
      const view = prefs.get("view");
      if (VIEW_MODES.includes(view) && view !== viewMode()) {
        setViewMode(view, { resizeWindow: true });
      }
      const onTop = $("#btn-on-top");
      if (onTop) onTop.setAttribute("aria-pressed", String(prefs.get("on_top") === true));
      await restorePeekFollow();
    } catch (err) {
      console.error("kit prefs restore failed", err);
    }

    setBootPhase("Connecting to listener…", { sticky: true });
    try {
      await refresh();
    } catch (err) {
      console.error("kit refresh failed", err);
    }

    setBootPhase("Opening live events…", { sticky: true });
    startEvents();
    schedulePoll();

    setBootPhase(
      state.online
        ? "Ready"
        : state.message && state.message !== "Connecting…"
          ? state.message
          : "Listener up · waiting for switch",
      { sticky: false }
    );
    // Drop the ticker once a real banner takes the LCD, or after the auto-clear.
    const watch = setInterval(() => {
      const banner = liveBanner();
      if (banner && banner.showing) {
        clearBootPhase();
        clearInterval(watch);
      } else if (!state.bootPhase) {
        clearInterval(watch);
      }
    }, 200);
    setTimeout(() => clearInterval(watch), 3000);
  })();

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
})();
