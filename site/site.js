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

  document.querySelectorAll("[data-kit-banner]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const kind = btn.getAttribute("data-kit-banner");
      if (kind === "dismiss") {
        send({ type: "kit-demo-dismiss" });
        return;
      }
      const samples = {
        door: { text: "Door unlocked", seconds: 8 },
        meeting: { text: "Meeting in 5", seconds: 12 },
        sticky: { text: "Stay on camera", sticky: true },
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
    setPressed("view", "compact");
    setPressed("body", "black");
  });
})();
