/* TESmart control panel.
 *
 * Api      – talks to /api/... and raises on error payloads.
 * Toaster  – Bootstrap toasts for every outcome; the browser's own dialogs are never used.
 * Panel    – owns the page: renders state, wires controls, runs the optional auto cycle.
 */

"use strict";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

class Api {
  async get(path) {
    return this.#request("GET", path);
  }

  async post(path, body) {
    return this.#request("POST", path, body);
  }

  /* Status and body exactly as returned, including error responses. */
  async exchange(method, path, bodyText) {
    const init = { method, headers: {} };
    if (bodyText) {
      init.headers["Content-Type"] = "application/json";
      init.body = bodyText;
    }
    const started = performance.now();
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      throw new ApiError("listener not reachable", 0);
    }
    return {
      status: response.status,
      ms: Math.round(performance.now() - started),
      text: await response.text(),
    };
  }

  async #request(method, path, body) {
    const init = { method, headers: {} };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      throw new ApiError("listener not reachable", 0);
    }
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      /* non-JSON body; fall through with the status text */
    }
    if (!response.ok) {
      throw new ApiError(payload.error || response.statusText, response.status);
    }
    return payload;
  }
}

class Toaster {
  constructor(container) {
    this.container = container;
  }

  info(message) {
    this.#show(message, "text-bg-dark");
  }

  success(message) {
    this.#show(message, "text-bg-success");
  }

  error(message) {
    this.#show(message, "text-bg-danger", 8000);
  }

  #show(message, cls, delay = 3500) {
    const element = document.createElement("div");
    element.className = `toast align-items-center border-0 ${cls}`;
    element.setAttribute("role", "status");
    element.setAttribute("aria-live", "polite");
    element.innerHTML =
      '<div class="d-flex"><div class="toast-body"></div>' +
      '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>';
    element.querySelector(".toast-body").textContent = message;
    this.container.appendChild(element);
    element.addEventListener("hidden.bs.toast", () => element.remove());
    bootstrap.Toast.getOrCreateInstance(element, { delay }).show();
  }
}

/* The address-change dialog. Three steps: warning + checkbox, risk/liability + checkbox, read-back. */
class NetworkWizard {
  constructor(modalElement, onConfirm) {
    this.modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    this.root = modalElement;
    this.onConfirm = onConfirm;
    this.values = null;
    this.el = {
      summary: modalElement.querySelector("#network-modal-summary"),
      ack1: modalElement.querySelector("#net-ack-1"),
      ack2: modalElement.querySelector("#net-ack-2"),
      cancel: modalElement.querySelector("#network-cancel"),
      next: modalElement.querySelector("#network-continue"),
      confirm: modalElement.querySelector("#network-confirm"),
      done: modalElement.querySelector("#network-done"),
      resultAlert: modalElement.querySelector("#network-result-alert"),
      resultRows: modalElement.querySelector("#network-result-rows"),
      resultNote: modalElement.querySelector("#network-result-note"),
    };
    this.el.ack1.addEventListener("change", () => { this.el.next.disabled = !this.el.ack1.checked; });
    this.el.ack2.addEventListener("change", () => { this.el.confirm.disabled = !this.el.ack2.checked; });
    this.el.next.addEventListener("click", () => this.#show(2));
    this.el.confirm.addEventListener("click", () => {
      this.el.confirm.disabled = true;
      this.el.confirm.textContent = "Writing…";
      this.onConfirm(this.values);
    });
  }

  static LABELS = { ip: "IP address", port: "Port", gateway: "Gateway", mask: "Mask" };

  open(values) {
    this.values = values;
    this.el.ack1.checked = false;
    this.el.ack2.checked = false;
    this.el.confirm.textContent = "Write to switch";
    this.el.summary.replaceChildren(
      ...Object.entries(values).flatMap(([key, value]) => {
        const dt = document.createElement("dt");
        dt.className = "col-4";
        dt.textContent = NetworkWizard.LABELS[key] || key;
        const dd = document.createElement("dd");
        dd.className = "col-8 font-monospace";
        dd.textContent = value;
        return [dt, dd];
      }),
    );
    this.#show(1);
    this.modal.show();
  }

  showResult(result) {
    const ok = Boolean(result.all_verified);
    this.el.resultAlert.className = `alert ${ok ? "alert-success" : "alert-danger"}`;
    this.el.resultAlert.textContent = ok
      ? "Confirmed: the switch reports the new values. They are stored and will be used after the next power cycle."
      : "Not confirmed: the switch reports something different from what was requested. Do not power-cycle until Query shows the values you expect.";
    this.el.resultRows.replaceChildren(
      ...Object.keys(result.requested || {}).map((key) => {
        const row = document.createElement("tr");
        const verified = Boolean(result.verified?.[key]);
        const cells = [
          NetworkWizard.LABELS[key] || key,
          result.requested[key],
          result.stored?.[key] ?? "no reply",
        ].map((text, index) => {
          const td = document.createElement("td");
          td.textContent = text;
          if (index > 0) td.className = "font-monospace";
          return td;
        });
        const badge = document.createElement("td");
        badge.innerHTML = `<span class="badge ${verified ? "text-bg-success" : "text-bg-danger"}">${verified ? "stored" : "mismatch"}</span>`;
        row.append(...cells, badge);
        return row;
      }),
    );
    this.el.resultNote.textContent = ok
      ? "The switch is still answering on its current address. After the power cycle, connect to the new address and update the default host if you saved one."
      : "You can retry with corrected values, or leave the switch as it is: nothing changes until it is power-cycled.";
    this.#show(3);
  }

  showError(message) {
    this.el.resultAlert.className = "alert alert-danger";
    this.el.resultAlert.textContent = `The write failed: ${message}`;
    this.el.resultRows.replaceChildren();
    this.el.resultNote.textContent = "Nothing was confirmed. Use Query to see what the switch currently holds.";
    this.#show(3);
  }

  #show(step) {
    this.root.querySelectorAll("[data-step]").forEach((section) => {
      section.classList.toggle("d-none", Number(section.dataset.step) !== step);
    });
    this.el.next.classList.toggle("d-none", step !== 1);
    this.el.confirm.classList.toggle("d-none", step !== 2);
    this.el.done.classList.toggle("d-none", step !== 3);
    this.el.cancel.classList.toggle("d-none", step === 3);
    this.el.next.disabled = !this.el.ack1.checked;
    this.el.confirm.disabled = !this.el.ack2.checked;
  }
}

class Panel {
  static POLL_MS = 5000;

  constructor(api, toaster) {
    this.api = api;
    this.toaster = toaster;
    this.state = null;
    this.busy = false;
    this.pollTimer = null;
    this.cycleTimer = null;
    this.peekTimer = null;
    this.peekRefresh = null;

    this.el = {
      endpoint: document.getElementById("endpoint"),
      link: document.getElementById("link"),
      activeLabel: document.getElementById("active-label"),
      inputs: document.getElementById("inputs"),
      previous: document.getElementById("btn-previous"),
      next: document.getElementById("btn-next"),
      autoCycle: document.getElementById("auto-cycle"),
      autoCycleInfo: document.getElementById("auto-cycle-info"),
      peekMode: document.getElementById("peek-mode"),
      peekSeconds: document.getElementById("peek-seconds"),
      peekStatus: document.getElementById("peek-status"),
      peekText: document.getElementById("peek-text"),
      peekBack: document.getElementById("btn-peek-back"),
      namesForm: document.getElementById("names-form"),
      namesSaved: document.getElementById("names-saved"),
      cycleForm: document.getElementById("cycle-form"),
      cycleSaved: document.getElementById("cycle-saved"),
      cycleInputs: document.getElementById("cycle-inputs"),
      cycleSeconds: document.getElementById("cycle-seconds"),
      networkView: document.getElementById("network-view"),
      networkForm: document.getElementById("network-form"),
      networkQuery: document.getElementById("btn-network-query"),
      lastSent: {
        buzzer: document.getElementById("buzzer-last"),
        led: document.getElementById("led-last"),
        autodetect: document.getElementById("autodetect-last"),
      },
    };
    this.wizard = new NetworkWizard(document.getElementById("network-modal"), (values) => this.applyNetwork(values));
    this.cycleSnapshot = null;
    this.namesSnapshot = null;
    this.cycleSaveTimer = null;
    this.namesSaveTimer = null;
    this.pendingNames = {};
  }

  // --- lifecycle -----------------------------------------------------------

  async start() {
    this.#wire();
    await this.refresh(true);
    this.#schedulePoll();
  }

  #wire() {
    this.el.inputs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-input]");
      if (button) this.selectInput(Number(button.dataset.input));
    });
    this.el.previous.addEventListener("click", () => this.step("previous"));
    this.el.next.addEventListener("click", () => this.step("next"));
    this.el.autoCycle.addEventListener("change", () => this.#applyAutoCycle());
    this.el.peekBack.addEventListener("click", () => this.cancelPeek());
    this.el.peekSeconds.addEventListener("change", () => this.savePeekSeconds());
    document.querySelectorAll("[data-follow]").forEach((button) => {
      button.addEventListener("click", () => this.setFollow(button.dataset.follow));
    });

    document.querySelectorAll("[data-setting]").forEach((group) => {
      group.addEventListener("click", (event) => {
        const button = event.target.closest("[data-value]");
        if (button) this.sendSetting(group.dataset.setting, button.dataset.value);
      });
    });

    // Names and cycle are local settings: save on change, no button.
    this.el.namesForm.addEventListener("submit", (event) => event.preventDefault());
    this.el.namesForm.addEventListener("change", (event) => {
      const field = event.target.closest("input[data-number]");
      if (field) this.queueNameSave(Number(field.dataset.number), field.value.trim());
    });
    this.el.cycleForm.addEventListener("submit", (event) => event.preventDefault());
    this.el.cycleForm.addEventListener("change", () => this.queueCycleSave());
    this.el.networkQuery.addEventListener("click", () => this.queryNetwork());
    this.el.networkForm.addEventListener("submit", (event) => {
      event.preventDefault();
      this.askNetwork();
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        this.#stopPoll();
      } else {
        this.refresh(false);
        this.#schedulePoll();
      }
    });
  }

  #schedulePoll() {
    this.#stopPoll();
    this.pollTimer = setInterval(() => {
      if (!this.busy) this.refresh(false);
    }, Panel.POLL_MS);
  }

  #stopPoll() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  // --- talking to the listener --------------------------------------------

  async #call(work, { silent = false, onError = null } = {}) {
    if (this.busy) {
      if (!silent) this.toaster.info("Still waiting for the switch…");
      if (onError) onError(new ApiError("still waiting for the switch", 0));
      return undefined;
    }
    this.busy = true;
    this.#setLink("busy");
    try {
      const result = await work();
      this.#setLink("ok");
      return result;
    } catch (error) {
      this.#setLink(error.status === 0 || error.status === 502 ? "down" : "ok");
      if (onError) onError(error);
      else if (!silent) this.toaster.error(error.message);
      return undefined;
    } finally {
      this.busy = false;
    }
  }

  async refresh(includeNetwork) {
    const state = await this.#call(
      () => this.api.get(`/api/state?network=${includeNetwork ? 1 : 0}`),
      { silent: !includeNetwork },
    );
    if (!state) return;
    const network = state.network || (this.state && this.state.network);
    this.state = { ...state, network };
    this.render();
  }

  async selectInput(number) {
    if (this.el.peekMode.checked) {
      await this.peek(number);
      return;
    }
    const result = await this.#call(() => this.api.post("/api/input", { input: number }));
    if (result) this.#applyInputResult(result);
  }

  // --- peek: flip to an input briefly, then back --------------------------

  async peek(number) {
    const seconds = Number(this.el.peekSeconds.value);
    if (!Number.isInteger(seconds) || seconds < 1) {
      this.toaster.error("Peek duration must be a whole number of seconds, 1 or more.");
      return;
    }
    const result = await this.#call(() => this.api.post("/api/peek", { input: number, seconds }));
    if (!result) return;
    this.state.active_input = result.active_input;
    this.state.active_name = result.active_name || null;
    this.state.peek = { ...(this.state.peek || {}), active: result.peek };
    this.renderInputs();
    this.renderPeek();
  }

  async cancelPeek() {
    const result = await this.#call(() => this.api.post("/api/peek", { cancel: true }));
    if (!result) return;
    this.state.peek = { ...(this.state.peek || {}), active: null };
    this.renderPeek();
    this.#refreshSoon(2500);
  }

  async savePeekSeconds() {
    const seconds = Number(this.el.peekSeconds.value);
    if (!Number.isInteger(seconds) || seconds < 1) {
      this.toaster.error("Peek duration must be a whole number of seconds, 1 or more.");
      return;
    }
    const result = await this.#call(() => this.api.post("/api/peek-default", { seconds }));
    if (!result) return;
    this.state.peek = { ...(this.state.peek || {}), seconds: result.peek.seconds };
    this.toaster.success(`Peek duration saved: ${seconds} s.`);
  }

  #refreshSoon(ms) {
    if (this.peekRefresh) clearTimeout(this.peekRefresh);
    this.peekRefresh = setTimeout(() => {
      this.peekRefresh = null;
      this.refresh(false);
    }, ms);
  }

  get peeking() {
    return Boolean(this.state?.peek?.active);
  }

  async step(direction) {
    const result = await this.#call(() => this.api.post(`/api/${direction}`, {}));
    if (result) this.#applyInputResult(result);
  }

  async setFollow(mode) {
    const result = await this.#call(() => this.api.post("/api/peek-follow", { mode }));
    if (!result) return;
    this.state.peek = { ...(this.state.peek || {}), follow: result.peek_follow };
    this.renderFollow();
    const note = {
      once: "The next input change will peek, then stick again.",
      always: "Input changes will peek until you turn this off.",
      off: "Input changes stick.",
    };
    this.toaster.success(note[mode] || "Peek follow updated.");
  }

  #applyInputResult(result) {
    this.state.active_input = result.active_input;
    this.state.active_name = result.active_name || null;
    if (result.peek_follow) {
      this.state.peek = { ...(this.state.peek || {}), follow: result.peek_follow };
    }
    if (result.peek) {
      this.state.peek = { ...(this.state.peek || {}), active: result.peek };
      this.renderPeek();
    }
    this.renderInputs();
    this.renderFollow();
    if (result.requested !== undefined && result.requested !== result.active_input && !result.peek) {
      this.toaster.error(`Asked for input ${result.requested}; the switch reports ${result.active_input}.`);
    }
  }

  async sendSetting(setting, value) {
    const routes = {
      buzzer: ["/api/buzzer", { on: value === "on" }, "Buzzer"],
      led: ["/api/led", { timeout: value }, "Display timeout"],
      autodetect: ["/api/autodetect", { on: value === "on" }, "Auto input detection"],
    };
    const [path, body, label] = routes[setting];
    const result = await this.#call(() => this.api.post(path, body));
    if (!result) return;
    const sent = this.state.last_sent || {};
    if (setting === "buzzer") sent.buzzer = result.buzzer;
    if (setting === "led") sent.led_timeout = result.led_timeout;
    if (setting === "autodetect") sent.auto_detect = result.auto_detect;
    this.state.last_sent = sent;
    this.renderSettings();
    this.toaster.success(`${label} sent.`);
  }

  // --- local settings: saved on change ------------------------------------

  queueNameSave(number, label) {
    this.pendingNames[String(number)] = label;
    clearTimeout(this.namesSaveTimer);
    this.namesSaveTimer = setTimeout(() => this.saveNames(), 300);
  }

  async saveNames() {
    if (this.busy) {
      this.namesSaveTimer = setTimeout(() => this.saveNames(), 300);
      return;
    }
    const names = this.pendingNames;
    this.pendingNames = {};
    if (Object.keys(names).length === 0) return;
    const result = await this.#call(() => this.api.post("/api/names", { names }), {
      onError: (error) => {
        this.toaster.error(`Name not saved: ${error.message}`);
        this.pendingNames = { ...names, ...this.pendingNames };
      },
    });
    if (!result) return;
    this.state.names = result.names;
    this.state.inputs = this.state.inputs.map((item) => ({
      ...item,
      name: result.names[String(item.number)] || null,
    }));
    this.state.active_name = result.names[String(this.state.active_input)] || null;
    this.renderInputs();
    this.renderNames();
    this.renderCycle();
    this.#flashSaved(this.el.namesSaved);
  }

  queueCycleSave() {
    clearTimeout(this.cycleSaveTimer);
    this.cycleSaveTimer = setTimeout(() => this.saveCycle(), 400);
  }

  async saveCycle() {
    if (this.busy) {
      this.cycleSaveTimer = setTimeout(() => this.saveCycle(), 300);
      return;
    }
    const inputs = [...this.el.cycleInputs.querySelectorAll("input:checked")].map((box) => Number(box.value));
    const mode = this.el.cycleForm.querySelector("input[name=cycle-mode]:checked")?.value || "manual";
    const seconds = Number(this.el.cycleSeconds.value);
    if (!Number.isInteger(seconds) || seconds < 1) {
      this.toaster.error("Interval must be a whole number of seconds, 1 or more.");
      return;
    }
    const result = await this.#call(() => this.api.post("/api/cycle", { inputs, mode, seconds }), {
      onError: (error) => this.toaster.error(`Cycle not saved: ${error.message}`),
    });
    if (!result) return;
    this.state.rotate = result.rotate;
    this.renderCycle();
    this.#applyAutoCycle();
    this.#flashSaved(this.el.cycleSaved);
  }

  #flashSaved(element) {
    element.textContent = "Saved";
    element.classList.add("is-visible");
    clearTimeout(element.hideTimer);
    element.hideTimer = setTimeout(() => element.classList.remove("is-visible"), 1500);
  }

  async queryNetwork() {
    const result = await this.#call(() => this.api.get("/api/network"));
    if (!result) return;
    this.state.network = result.network;
    this.renderNetwork();
    this.toaster.success("Network settings read from the switch.");
  }

  askNetwork() {
    const values = {};
    for (const key of ["ip", "port", "gateway", "mask"]) {
      const value = document.getElementById(`net-${key}`).value.trim();
      if (value) values[key] = value;
    }
    if (Object.keys(values).length === 0) {
      this.toaster.error("Enter at least one value to change.");
      return;
    }
    this.wizard.open(values);
  }

  /* Called by the wizard after both acknowledgements. The server requires both flags too. */
  async applyNetwork(values) {
    const result = await this.#call(
      () => this.api.post("/api/network", { ...values, confirm: true, accept_risk: true }),
      { onError: (error) => this.wizard.showError(error.message) },
    );
    if (!result) return;
    this.wizard.showResult(result);
    if (result.stored) {
      this.state.network = result.stored;
      this.renderNetwork();
    }
    this.el.networkForm.reset();
  }

  // --- auto cycle (runs in the browser) -----------------------------------

  #applyAutoCycle() {
    if (this.cycleTimer) clearInterval(this.cycleTimer);
    this.cycleTimer = null;
    const rotate = this.state?.rotate || {};
    const seconds = rotate.seconds || Number(this.el.cycleSeconds.value) || 10;
    if (this.el.autoCycle.checked) {
      this.cycleTimer = setInterval(() => {
        if (!this.busy && !document.hidden && !this.peeking) this.step("next");
      }, seconds * 1000);
      this.el.autoCycleInfo.textContent = `every ${seconds} s`;
    } else {
      this.el.autoCycleInfo.textContent = rotate.mode === "automatic" ? `(${seconds} s saved)` : "";
    }
  }

  // --- rendering -----------------------------------------------------------

  render() {
    this.el.endpoint.textContent = this.state.endpoint || "";
    this.renderInputs();
    this.renderPeek();
    this.renderFollow();
    this.renderSettings();
    this.renderNames();
    this.renderCycle();
    this.renderNetwork();
    if (!this.el.autoCycle.dataset.initialised) {
      this.el.autoCycle.dataset.initialised = "1";
      this.#applyAutoCycle();
    }
  }

  renderInputs() {
    const active = this.state.active_input;
    const label = this.state.active_name ? `${active} · ${this.state.active_name}` : `${active}`;
    this.el.activeLabel.textContent = label;

    const inputs = this.state.inputs || [];
    if (this.el.inputs.childElementCount !== inputs.length) {
      this.el.inputs.replaceChildren(
        ...inputs.map((item) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "btn input-btn";
          button.dataset.input = String(item.number);
          const number = document.createElement("span");
          number.className = "input-number";
          number.textContent = String(item.number);
          const name = document.createElement("span");
          name.className = "input-name";
          button.append(number, name);
          return button;
        }),
      );
    }
    // Update in place so focus and hover survive the periodic refresh.
    const peeked = this.state.peek?.active?.peeked;
    inputs.forEach((item, index) => {
      const button = this.el.inputs.children[index];
      const isActive = item.number === active;
      button.classList.toggle("btn-primary", isActive);
      button.classList.toggle("is-active", isActive);
      button.classList.toggle("is-peek", isActive && item.number === peeked);
      button.classList.toggle("btn-outline-secondary", !isActive);
      button.setAttribute("aria-pressed", String(isActive));
      button.querySelector(".input-name").textContent = item.name || `Input ${item.number}`;
    });
  }

  renderPeek() {
    const peek = this.state.peek || {};
    if (document.activeElement !== this.el.peekSeconds && peek.seconds && !this.peeking) {
      this.el.peekSeconds.value = peek.seconds;
    }
    if (this.peekTimer) clearInterval(this.peekTimer);
    this.peekTimer = null;

    const active = peek.active;
    if (!active) {
      this.el.peekStatus.classList.add("d-none");
      return;
    }
    const names = this.state.names || {};
    const label = (n) => (names[String(n)] ? `${n} · ${names[String(n)]}` : String(n));
    let remaining = Math.ceil(active.remaining);
    const draw = () => {
      this.el.peekText.textContent = `${label(active.peeked)} → back to ${label(active.previous)} in ${remaining} s`;
    };
    draw();
    this.el.peekStatus.classList.remove("d-none");
    this.peekTimer = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(this.peekTimer);
        this.peekTimer = null;
        this.el.peekText.textContent = `returning to ${label(active.previous)}…`;
        this.#refreshSoon(2500);
        return;
      }
      draw();
    }, 1000);
  }

  renderFollow() {
    const mode = (this.state.peek && this.state.peek.follow) || "off";
    document.querySelectorAll("[data-follow]").forEach((button) => {
      const on = button.dataset.follow === mode;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  renderSettings() {
    const sent = this.state.last_sent || {};
    const highlight = (setting, value) => {
      document.querySelectorAll(`[data-setting="${setting}"] [data-value]`).forEach((button) => {
        const on = value !== null && value !== undefined && button.dataset.value === value;
        button.classList.toggle("btn-primary", on);
        button.classList.toggle("btn-outline-secondary", !on);
      });
    };
    const flag = (value) => (value === null || value === undefined ? null : value ? "on" : "off");
    const led = (value) => {
      if (value === null || value === undefined) return null;
      const text = String(value).toLowerCase();
      if (text.startsWith("never")) return "never";
      if (text.startsWith("10")) return "10";
      if (text.startsWith("30")) return "30";
      return null;
    };
    highlight("buzzer", flag(sent.buzzer));
    highlight("led", led(sent.led_timeout));
    highlight("autodetect", flag(sent.auto_detect));

    const unknown = "not sent yet";
    this.el.lastSent.buzzer.textContent = sent.buzzer == null ? unknown : sent.buzzer ? "last sent: on" : "last sent: off";
    this.el.lastSent.led.textContent = sent.led_timeout == null ? unknown : `last sent: ${sent.led_timeout}`;
    this.el.lastSent.autodetect.textContent =
      sent.auto_detect == null ? unknown : sent.auto_detect ? "last sent: on" : "last sent: off";
  }

  renderNames() {
    // Rebuild only when the saved names change (a save here, or an edit from the CLI).
    // Otherwise leave the fields alone so the periodic refresh cannot discard typing.
    const inputs = this.state.inputs || [];
    const snapshot = JSON.stringify(inputs.map((item) => [item.number, item.name || ""]));
    if (snapshot === this.namesSnapshot) return;
    this.namesSnapshot = snapshot;
    if (this.el.namesForm.childElementCount === inputs.length) {
      // Update in place; never touch the field being typed in.
      inputs.forEach((item) => {
        const field = this.el.namesForm.querySelector(`input[data-number="${item.number}"]`);
        if (field && document.activeElement !== field) field.value = item.name || "";
      });
      return;
    }
    const rows = inputs.map((item) => {
      const group = document.createElement("div");
      group.className = "input-group input-group-sm names-row";
      const prefix = document.createElement("span");
      prefix.className = "input-group-text";
      prefix.textContent = String(item.number);
      const field = document.createElement("input");
      field.type = "text";
      field.className = "form-control";
      field.dataset.number = String(item.number);
      field.value = item.name || "";
      field.placeholder = `Input ${item.number}`;
      field.maxLength = 40;
      field.setAttribute("aria-label", `Name for input ${item.number}`);
      group.append(prefix, field);
      return group;
    });
    this.el.namesForm.replaceChildren(...rows);
  }

  renderCycle() {
    const rotate = this.state.rotate || {};
    const inputs = this.state.inputs || [];
    const chosen = rotate.configured ? rotate.cycle : [];
    const mode = rotate.mode === "automatic" ? "automatic" : "manual";

    // The form holds unsaved choices between polls. Rebuild it only when the
    // saved cycle itself changes (a save here, or `tesmartctl cycle` elsewhere).
    const snapshot = JSON.stringify([chosen, mode, rotate.seconds || null, inputs.length]);
    if (snapshot !== this.cycleSnapshot) {
      this.cycleSnapshot = snapshot;
      const chosenSet = new Set(chosen);
      this.el.cycleInputs.replaceChildren(
        ...inputs.map((item) => {
          const wrapper = document.createElement("div");
          const box = document.createElement("input");
          box.type = "checkbox";
          box.className = "btn-check";
          box.id = `cycle-${item.number}`;
          box.value = String(item.number);
          box.autocomplete = "off";
          box.checked = chosenSet.has(item.number);
          const label = document.createElement("label");
          label.className = "btn btn-outline-secondary btn-sm";
          label.htmlFor = box.id;
          wrapper.append(box, label);
          return wrapper;
        }),
      );
      const radio = this.el.cycleForm.querySelector(`input[name=cycle-mode][value=${mode}]`);
      if (radio) radio.checked = true;
      if (rotate.seconds) this.el.cycleSeconds.value = rotate.seconds;
    }

    // Labels follow the names even when the selection is left untouched.
    inputs.forEach((item) => {
      const label = this.el.cycleInputs.querySelector(`label[for="cycle-${item.number}"]`);
      if (label) label.textContent = item.name ? `${item.number} · ${item.name}` : String(item.number);
    });
  }

  renderNetwork() {
    const network = this.state.network || {};
    this.el.networkView.querySelectorAll("[data-net]").forEach((cell) => {
      cell.textContent = network[cell.dataset.net] || "—";
    });
  }

  #setLink(mode) {
    const link = this.el.link;
    link.classList.remove("text-bg-secondary", "text-bg-success", "text-bg-danger", "is-busy");
    const text = link.childNodes[link.childNodes.length - 1];
    if (mode === "busy") {
      link.classList.add("text-bg-success", "is-busy");
      text.textContent = " talking";
    } else if (mode === "down") {
      link.classList.add("text-bg-danger");
      text.textContent = " unreachable";
    } else {
      link.classList.add("text-bg-success");
      text.textContent = " connected";
    }
  }
}

/* Lists the routes from GET /api and shows the raw status and body of whatever is sent. */
class Explorer {
  constructor(api, onChanged) {
    this.api = api;
    this.onChanged = onChanged;
    this.catalog = [];
    this.selected = null;
    this.sending = false;
    this.el = {
      list: document.getElementById("api-list"),
      method: document.getElementById("api-method"),
      path: document.getElementById("api-path"),
      body: document.getElementById("api-body"),
      send: document.getElementById("api-send"),
      hint: document.getElementById("api-hint"),
      status: document.getElementById("api-status"),
      response: document.getElementById("api-response"),
    };
  }

  start() {
    this.el.list.addEventListener("click", (event) => {
      const item = event.target.closest("[data-index]");
      if (item) this.select(Number(item.dataset.index));
    });
    this.el.send.addEventListener("click", () => this.send());
    document.getElementById("api-modal").addEventListener("show.bs.modal", () => {
      if (this.catalog.length === 0) this.load();
    });
  }

  async load() {
    this.el.list.textContent = "Loading…";
    try {
      const payload = await this.api.get("/api");
      this.catalog = payload.catalog || [];
    } catch (error) {
      this.el.list.textContent = error.message;
      return;
    }
    this.el.list.replaceChildren(
      ...this.catalog.map((route, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "list-group-item list-group-item-action";
        button.dataset.index = String(index);
        const method = document.createElement("span");
        method.className = `badge me-2 ${route.method === "GET" ? "text-bg-secondary" : "text-bg-primary"}`;
        method.textContent = route.method;
        const path = document.createElement("span");
        path.className = "font-monospace";
        path.textContent = route.path;
        const summary = document.createElement("div");
        summary.className = "small text-body-secondary";
        summary.textContent = route.summary;
        button.append(method, path, summary);
        return button;
      }),
    );
    if (this.catalog.length) this.select(0);
  }

  select(index) {
    this.selected = this.catalog[index];
    [...this.el.list.children].forEach((item, i) => item.classList.toggle("active", i === index));
    const route = this.selected;
    this.el.method.textContent = route.method;
    this.el.path.value = route.path;
    this.el.body.value = route.sample === undefined ? "" : JSON.stringify(route.sample, null, 2);
    this.el.body.disabled = route.method === "GET";
    this.el.send.disabled = Boolean(route.blocked);
    this.el.send.textContent = route.blocked ? "Not from here" : "Send";
    this.el.hint.textContent = route.blocked
      ? "Address changes are not sent from this dialog. Use Change address, which warns twice and then reads the values back."
      : route.summary;
  }

  async send() {
    if (this.sending) return;
    const method = this.el.method.textContent;
    const path = this.el.path.value.trim();
    if (method === "POST" && path.split("?")[0] === "/api/network") {
      this.#showResult(null, "Not sent. Address changes go through Change address, which warns twice and reads the values back.");
      return;
    }
    let bodyText = "";
    if (method !== "GET" && this.el.body.value.trim()) {
      try {
        bodyText = JSON.stringify(JSON.parse(this.el.body.value));
      } catch (error) {
        this.#showResult(null, `Body is not valid JSON: ${error.message}`);
        return;
      }
    }
    this.sending = true;
    this.el.send.disabled = true;
    this.el.status.textContent = "sending…";
    try {
      const result = await this.api.exchange(method, path, bodyText);
      this.#showResult(result);
      if (method !== "GET" && this.onChanged) this.onChanged();
    } catch (error) {
      this.#showResult(null, error.message);
    } finally {
      this.sending = false;
      this.el.send.disabled = Boolean(this.selected && this.selected.blocked);
    }
  }

  #showResult(result, note) {
    if (!result) {
      this.el.status.textContent = "";
      this.el.response.textContent = note;
      return;
    }
    this.el.status.textContent = `${result.status}  ${result.ms} ms`;
    this.el.status.className = `small font-monospace ${result.status < 400 ? "text-success" : "text-danger"}`;
    let text = result.text;
    try {
      text = JSON.stringify(JSON.parse(result.text), null, 2);
    } catch (error) {
      /* leave non-JSON bodies as they arrived */
    }
    this.el.response.textContent = text || "(empty body)";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const api = new Api();
  const panel = new Panel(api, new Toaster(document.getElementById("toasts")));
  panel.start();
  new Explorer(api, () => panel.refresh(false)).start();
});
