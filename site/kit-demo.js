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
  const nameEl = $("#name");

  const VIEWS = ["compact", "mini", "full"];
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

  const state = {
    count: 8,
    active: 2,
    view: "compact",
    body: "black",
    banner: null,
    bannerUntil: 0,
    bannerTimer: 0,
  };

  function postHeight() {
    const height = Math.ceil(document.documentElement.scrollHeight);
    parent.postMessage({ type: "kit-demo-size", height, view: state.view }, "*");
  }

  function setBody(body) {
    state.body = body === "silver" ? "silver" : "black";
    bodyEl.dataset.body = state.body;
    deck.dataset.body = state.body;
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

  function showBanner(text, { sticky = false, seconds = 8 } = {}) {
    const line = String(text || "").trim().slice(0, 160);
    if (!line) return;
    clearBannerTimer();
    state.banner = {
      text: line,
      sticky: Boolean(sticky),
      seconds: Math.max(1, Math.min(60, Number(seconds) || 8)),
    };
    if (!state.banner.sticky) {
      state.bannerUntil = performance.now() + state.banner.seconds * 1000;
      state.bannerTimer = setTimeout(() => {
        state.banner = null;
        draw();
      }, state.banner.seconds * 1000);
    } else {
      state.bannerUntil = 0;
    }
    draw();
  }

  function hideBanner() {
    clearBannerTimer();
    state.banner = null;
    draw();
  }

  function draw() {
    const banner = state.banner;
    const showing = Boolean(banner);
    nameEl.classList.toggle("is-banner", showing);
    if (showing) {
      nameTrack.textContent = banner.text;
      statusEl.textContent = banner.sticky
        ? "HUBITAT • STICKY"
        : `HUBITAT • ${Math.max(1, Math.ceil((state.bannerUntil - performance.now()) / 1000))} SEC`;
      detailEl.textContent = "Notification from Hubitat";
      msgBtn.hidden = false;
      msgBtn.classList.add("is-active");
    } else {
      const label = NAMES[state.active] || `HDMI ${state.active}`;
      nameTrack.textContent = `${String(state.active).padStart(2, "0")}  ${label.toUpperCase()}`;
      statusEl.textContent = `INPUT ${state.active} SELECTED • DEMO`;
      detailEl.textContent = "Ready • PEEK OFF";
      msgBtn.hidden = true;
      msgBtn.classList.remove("is-active");
    }
    digits.textContent = "--";

    grid.replaceChildren();
    for (let n = 1; n <= state.count; n++) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "input" + (n === state.active ? " selected" : "");
      b.textContent = String(n);
      b.setAttribute("aria-pressed", String(n === state.active));
      b.setAttribute("aria-label", `Input ${n}: ${NAMES[n] || "HDMI " + n}`);
      b.addEventListener("click", () => {
        state.active = n;
        draw();
      });
      grid.append(b);
    }
    postHeight();
  }

  viewBtn.addEventListener("click", () => {
    const i = VIEWS.indexOf(state.view);
    setView(VIEWS[(i + 1) % VIEWS.length]);
    draw();
  });

  $("#mini-prev").addEventListener("click", () => {
    state.active = state.active === 1 ? state.count : state.active - 1;
    draw();
  });
  $("#mini-next").addEventListener("click", () => {
    state.active = state.active === state.count ? 1 : state.active + 1;
    draw();
  });

  msgBtn.addEventListener("click", () => hideBanner());

  $("#service-door").addEventListener("click", () => {
    const bay = $("#service-bay");
    const open = bay.dataset.open !== "true";
    bay.dataset.open = String(open);
    $("#service-bay-inner").hidden = !open;
    $("#service-door").setAttribute("aria-expanded", String(open));
    postHeight();
  });

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "kit-demo-view") setView(data.view);
    if (data.type === "kit-demo-body") setBody(data.body);
    if (data.type === "kit-demo-banner") {
      showBanner(data.text, { sticky: data.sticky, seconds: data.seconds });
    }
    if (data.type === "kit-demo-dismiss") hideBanner();
    if (data.type === "kit-demo-ping") postHeight();
  });

  window.addEventListener("resize", postHeight);
  setView("compact");
  setBody("black");
  draw();
  showBanner("Door unlocked", { seconds: 6 });
  setTimeout(postHeight, 120);
})();
