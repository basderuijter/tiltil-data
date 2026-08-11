/* Warehouse label scanner UI.
 *
 * Scan -> confirm parcels + shipping method -> label + print. The scanner
 * behaves as a keyboard that ends its input with Enter, so the scan screen just
 * keeps focus on one input and submits on Enter.
 */

const $ = (id) => document.getElementById(id);

const STATION_KEY = "label-scanner.station";

const state = {
  station: null,
  stations: [],
  order: null,
  options: [],
  quantity: 1,
  optionCode: null,
  lastResult: null,
};

let toastTimer = null;
let returnTimer = null;

// --- helpers ---------------------------------------------------------------

function showScreen(name) {
  for (const screen of ["station", "scan", "order", "result"]) {
    $(`screen-${screen}`).classList.toggle("hidden", screen !== name);
  }
  $("btn-home").classList.toggle("hidden", name === "scan" || name === "station");
  if (name === "scan") {
    $("input-order").value = "";
    $("input-order").focus();
  }
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.add("hidden"), 6000);
}

async function api(path, options = {}) {
  $("overlay").classList.remove("hidden");
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `Fout ${response.status}`);
    }
    return payload;
  } finally {
    $("overlay").classList.add("hidden");
  }
}

// --- step 1: scan ----------------------------------------------------------

$("form-scan").addEventListener("submit", async (event) => {
  event.preventDefault();
  const orderNumber = $("input-order").value.trim();
  if (!orderNumber) return;
  try {
    const data = await api(`/api/orders/${encodeURIComponent(orderNumber)}`);
    renderOrder(data);
    // The delivery already says how it ships, so there is nothing to confirm:
    // one scan, one label. The address still appears on the result screen.
    if (data.fast_mode) return makeLabels(1);
    showScreen("order");
  } catch (error) {
    toast(error.message);
    $("input-order").select();
  }
});

// Keep the scanner pointed at the input even if someone taps elsewhere.
document.addEventListener("click", () => {
  if (!$("screen-scan").classList.contains("hidden")) $("input-order").focus();
});

// --- step 0: which table is this? ------------------------------------------

async function initStations() {
  try {
    state.stations = await api("/api/stations");
  } catch {
    // A single-table setup has no station list; the server then falls back to
    // the printer in its own configuration.
    state.stations = [];
  }
  if (!state.stations.length) return showScreen("scan");

  const saved = localStorage.getItem(STATION_KEY);
  const known = state.stations.find((s) => s.id === saved);
  if (known) {
    selectStation(known);
    return showScreen("scan");
  }
  renderStations();
  showScreen("station");
}

function renderStations() {
  const container = $("station-buttons");
  container.innerHTML = "";
  for (const station of state.stations) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tile";
    button.textContent = station.name;
    button.addEventListener("click", () => {
      selectStation(station);
      showScreen("scan");
    });
    container.appendChild(button);
  }
}

function selectStation(station) {
  state.station = station;
  localStorage.setItem(STATION_KEY, station.id);
  const badge = $("btn-station");
  badge.textContent = station.name;
  badge.classList.remove("hidden");
}

// Changing the table is deliberate but rare, so it lives behind the badge.
$("btn-station").addEventListener("click", () => {
  renderStations();
  showScreen("station");
});

// --- step 2: confirm -------------------------------------------------------

function renderOrder(data) {
  state.order = data.order;
  state.options = data.shipping_options;
  state.quantity = 1;
  state.optionCode = data.order.current_shipping_option_code || null;

  $("badge-demo").classList.toggle("hidden", !data.demo_mode);
  $("order-number").textContent = data.order.order_number || "—";
  $("order-address").innerHTML = addressLines(data.order)
    .map((line) => escapeHtml(line))
    .join("<br />");
  $("warn-announced").classList.toggle("hidden", !data.order.already_announced);

  $("current-method").textContent = data.order.current_shipping_option_name
    ? `in de order: ${data.order.current_shipping_option_name}`
    : "geen methode in de order";

  renderQuantities(data.max_parcels);
  renderOptions();
  updateCreateButton();
}

function addressLines(order) {
  const lines = [order.recipient_name, order.company_name, order.address];
  lines.push(`${order.postal_code} ${order.city}`.trim());
  lines.push(order.country_name || order.country_code);
  if (order.weight_kg) lines.push(`${order.weight_kg} kg`);
  return lines.filter(Boolean);
}

function renderQuantities(max) {
  const container = $("quantity-buttons");
  container.innerHTML = "";
  for (let count = 1; count <= max; count += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tile tile--quantity";
    button.textContent = String(count);
    button.setAttribute("aria-pressed", String(count === state.quantity));
    button.addEventListener("click", () => {
      state.quantity = count;
      renderQuantities(max);
      updateCreateButton();
    });
    container.appendChild(button);
  }
}

function renderOptions() {
  const container = $("method-buttons");
  container.innerHTML = "";
  for (const method of state.options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tile";
    button.setAttribute("aria-pressed", String(method.code === state.optionCode));

    const name = document.createElement("span");
    name.textContent = method.name;
    button.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "tile__meta";
    meta.textContent = [method.carrier, method.code].filter(Boolean).join(" · ");
    if (method.is_current) {
      // Inline rather than a block badge, so every tile keeps the same height.
      const flag = document.createElement("span");
      flag.className = "tile__flag";
      flag.textContent = "staat in de order";
      meta.appendChild(flag);
    }
    button.appendChild(meta);

    button.addEventListener("click", () => {
      state.optionCode = method.code;
      renderOptions();
      updateCreateButton();
    });
    container.appendChild(button);
  }
}

function updateCreateButton() {
  const button = $("btn-create");
  button.disabled = !state.optionCode;
  button.textContent = state.optionCode
    ? `${state.quantity} label${state.quantity > 1 ? "s" : ""} maken & printen`
    : "Kies een verzendmethode";
}

async function makeLabels(quantity) {
  const result = await api("/api/labels", {
    method: "POST",
    body: JSON.stringify({
      order_number: state.order.order_number,
      shipping_option_code: state.optionCode,
      quantity,
      station: state.station?.id ?? null,
    }),
  });
  renderResult(result);
  showScreen("result");
}

$("btn-create").addEventListener("click", async () => {
  if (!state.optionCode) return;
  try {
    await makeLabels(state.quantity);
  } catch (error) {
    toast(error.message);
  }
});

// --- step 3: result --------------------------------------------------------

function renderResult(result) {
  state.lastResult = result;
  const printed = result.printed;
  const tracking = (result.labels || []).map((label) => label.tracking_number).filter(Boolean);

  $("result-icon").textContent = printed ? "✓" : "!";
  $("result-icon").classList.toggle("result-icon--warn", !printed);
  $("result-title").textContent = printed
    ? `${result.labels.length} label${result.labels.length > 1 ? "s" : ""} geprint`
    : "Label gemaakt, printen mislukt";
  $("result-detail").textContent = printed
    ? `${result.order_number} · ${result.shipping_option_name}`
    : result.print_error;
  $("result-address").innerHTML = state.order
    ? addressLines(state.order).map((line) => escapeHtml(line)).join(" · ")
    : "";

  $("result-tracking").innerHTML = tracking
    .map((code) => `<li>${escapeHtml(code)}</li>`)
    .join("");
  $("btn-reprint").classList.toggle("hidden", printed);

  // Hands are full in the warehouse: after a clean print, go back to scanning
  // on our own. Long enough to notice an extra box is needed and press the
  // button, short enough not to slow the next slip down.
  clearTimeout(returnTimer);
  if (printed) returnTimer = setTimeout(() => showScreen("scan"), 8000);
}

// Discovered mid-pack that it does not fit: one more box, one more label.
$("btn-extra").addEventListener("click", async () => {
  clearTimeout(returnTimer);
  try {
    const result = await api("/api/labels/extra", {
      method: "POST",
      body: JSON.stringify({
        order_number: state.order.order_number,
        shipping_option_code: state.lastResult.shipping_option_code,
        station: state.station?.id ?? null,
      }),
    });
    renderResult(result);
  } catch (error) {
    toast(error.message);
  }
});

$("btn-reprint").addEventListener("click", async () => {
  try {
    await api("/api/reprint", {
      method: "POST",
      body: JSON.stringify({
        result: state.lastResult,
        station: state.station?.id ?? null,
      }),
    });
    renderResult({ ...state.lastResult, printed: true, print_error: "" });
  } catch (error) {
    toast(error.message);
  }
});

$("btn-next").addEventListener("click", () => showScreen("scan"));
$("btn-home").addEventListener("click", () => showScreen("scan"));

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

initStations();
