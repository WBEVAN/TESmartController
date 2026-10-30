(() => {
  const frame = document.getElementById("kit-frame");
  if (!frame) return;

  const stage = document.getElementById("kit-stage");

  function send(payload) {
    if (!frame.contentWindow) return;
    frame.contentWindow.postMessage(payload, "*");
  }

  function setPressed(group, value) {
    document.querySelectorAll(`[data-kit-${group}]`).forEach((btn) => {
      const on = btn.getAttribute(`data-kit-${group}`) === value;
      btn.setAttribute("aria-pressed", String(on));
      btn.classList.toggle("is-active", on);
    });
  }

  document.querySelectorAll("[data-kit-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.getAttribute("data-kit-view");
      setPressed("view", view);
      send({ type: "kit-demo-view", view });
    });
  });

  document.querySelectorAll("[data-kit-body]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const body = btn.getAttribute("data-kit-body");
      setPressed("body", body);
      send({ type: "kit-demo-body", body });
    });
  });

  document.querySelectorAll("[data-kit-lcd]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const lcd = btn.getAttribute("data-kit-lcd");
      setPressed("lcd", lcd);
      send({ type: "kit-demo-lcd", lcd });
    });
  });

  document.querySelectorAll("[data-kit-bright]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const level = btn.getAttribute("data-kit-bright");
      setPressed("bright", level);
      send({ type: "kit-demo-brightness", level: Number(level) });
    });
  });

  document.querySelectorAll("[data-kit-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const on = btn.getAttribute("aria-pressed") !== "true";
      btn.setAttribute("aria-pressed", String(on));
      btn.classList.toggle("is-active", on);
      const which = btn.getAttribute("data-kit-toggle");
      if (which === "names") send({ type: "kit-demo-names", on });
      if (which === "hide-disabled") send({ type: "kit-demo-hide-disabled", on });
      if (which === "auto-dim") send({ type: "kit-demo-auto-dim", on });
    });
  });

  document.querySelectorAll("[data-kit-banner]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.getAttribute("data-kit-banner");
      if (kind === "dismiss") {
        send({ type: "kit-demo-dismiss" });
        return;
      }
      const samples = {
        door: { text: "Door unlocked", seconds: 8, from: "HUBITAT" },
        meeting: { text: "Meeting in 5", seconds: 12, from: "HUBITAT" },
        sticky: { text: "Stay on camera", sticky: true, from: "HUBITAT" },
      };
      const sample = samples[kind] || samples.door;
      send({ type: "kit-demo-banner", ...sample });
    });
  });

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.type !== "kit-demo-size") return;
    const height = Math.max(180, Math.min(720, Number(data.height) || 360));
    frame.style.height = `${height}px`;
    if (stage) stage.dataset.view = data.view || "";
  });

  frame.addEventListener("load", () => {
    send({ type: "kit-demo-ping" });
    document.querySelectorAll("[data-kit-toggle]").forEach((btn) => {
      const on = btn.getAttribute("aria-pressed") === "true";
      const which = btn.getAttribute("data-kit-toggle");
      if (which === "names") send({ type: "kit-demo-names", on });
      if (which === "hide-disabled") send({ type: "kit-demo-hide-disabled", on });
      if (which === "auto-dim") send({ type: "kit-demo-auto-dim", on });
    });
    const bright = document.querySelector("[data-kit-bright][aria-pressed='true']");
    if (bright) send({ type: "kit-demo-brightness", level: Number(bright.getAttribute("data-kit-bright")) });
    setPressed("view", "compact");
    setPressed("body", "black");
    setPressed("lcd", "green");
    setPressed("bright", "100");
  });
})();
