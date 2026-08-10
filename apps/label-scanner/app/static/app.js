/* Warehouse label scanner UI.
 *
 * Scan -> confirm parcels + shipping method -> label + print. The scanner
 * behaves as a keyboard that ends its input with Enter, so the scan screen just
 * keeps focus on one input and submits on Enter.
 */

const $ = (id) => document.getElementById(id);

const state = {
  order: null,
  options: [],
  quantity: 1,
  optionCode: null,
  lastResult: null,
};

let toastTimer = null;

// --- helpers ---------------------------------------------------------------

function showScreen(name) {
  for (const screen of ["scan", "order", "result"]) {
    $(`screen-${screen}`).classList.toggle("hidden", screen !== name);
  }
  $("btn-home").classList.toggle("hidden", name === "scan");
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

$("btn-create").addEventListener("click", async () => {
  if (!state.optionCode) return;
  try {
    const result = await api("/api/labels", {
      method: "POST",
      body: JSON.stringify({
        order_number: state.order.order_number,
        shipping_option_code: state.optionCode,
        quantity: state.quantity,
      }),
    });
    renderResult(result);
    showScreen("result");
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

  $("result-tracking").innerHTML = tracking
    .map((code) => `<li>${escapeHtml(code)}</li>`)
    .join("");
  $("btn-reprint").classList.toggle("hidden", printed);

  // Hands are full in the warehouse: after a clean print, go back to scanning
  // on our own so the next slip can be scanned straight away.
  if (printed) setTimeout(() => showScreen("scan"), 4000);
}

$("btn-reprint").addEventListener("click", async () => {
  try {
    await api("/api/reprint", {
      method: "POST",
      body: JSON.stringify(state.lastResult),
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

showScreen("scan");
