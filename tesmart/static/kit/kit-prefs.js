/* Remembered kit settings (view size, peek-follow, Always on top).
 *
 * In the frameless pywebview window localStorage is wiped every launch, so
 * settings go through the Python bridge (get_prefs / set_prefs →
 * ~/.config/tesmartctl/kit.json). Browser popups and Chromium --app use
 * localStorage. ?fresh=1 ignores what is saved for this launch.
 */
class KitPrefs {
  static STORAGE_KEY = "tesmart-kit-prefs";
  static BRIDGE_WAIT_MS = 1500;

  constructor({ isApp = false, fresh = false } = {}) {
    this.isApp = isApp;
    this.fresh = fresh;
    this.values = {};
  }

  static #bridge() {
    const api = window.pywebview && window.pywebview.api;
    return api && typeof api.get_prefs === "function" ? api : null;
  }

  #waitForBridge() {
    const ready = KitPrefs.#bridge();
    if (ready || !this.isApp) return Promise.resolve(ready);
    return new Promise((resolve) => {
      const done = () => {
        clearTimeout(timer);
        window.removeEventListener("pywebviewready", done);
        resolve(KitPrefs.#bridge());
      };
      const timer = setTimeout(done, KitPrefs.BRIDGE_WAIT_MS);
      window.addEventListener("pywebviewready", done);
    });
  }

  #readLocal() {
    if (this.fresh) return {};
    try {
      const parsed = JSON.parse(localStorage.getItem(KitPrefs.STORAGE_KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  #writeLocal() {
    try {
      localStorage.setItem(KitPrefs.STORAGE_KEY, JSON.stringify(this.values));
    } catch {
      /* storage disabled */
    }
  }

  async load() {
    const bridge = await this.#waitForBridge();
    if (bridge) {
      try {
        this.values = (await bridge.get_prefs()) || {};
        if (this.values.fresh) this.fresh = true;
        return this.values;
      } catch {
        /* fall back to localStorage */
      }
    }
    this.values = this.#readLocal();
    return this.values;
  }

  get(key, fallback = undefined) {
    return key in this.values ? this.values[key] : fallback;
  }

  async save(changes) {
    const payload = {};
    for (const [key, value] of Object.entries(changes || {})) {
      if (value === null || value === undefined) {
        delete this.values[key];
        payload[key] = null;
      } else {
        this.values[key] = value;
        payload[key] = value;
      }
    }
    const bridge = KitPrefs.#bridge();
    if (bridge && typeof bridge.set_prefs === "function") {
      try {
        await bridge.set_prefs(payload);
        return;
      } catch {
        /* fall back to localStorage */
      }
    }
    this.#writeLocal();
  }
}

window.KitPrefs = KitPrefs;
