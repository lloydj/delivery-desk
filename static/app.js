const ROLES = [
  ["client", "Client"],
  ["vendor", "Vendor"],
  ["courier", "Moto"],
];

const STATUS_LABEL = {
  placed: "Placed",
  accepted: "Accepted",
  ready: "Ready",
  claimed: "Claimed",
  picked_up: "Picked up",
  delivered: "Delivered",
  rejected: "Rejected",
};

const NEXT_LINE = {
  placed: "Waiting for the vendor to accept.",
  accepted: "The vendor is preparing this order.",
  ready: "Waiting for a moto guy to claim it.",
  claimed: "The moto guy is going to the stall.",
  picked_up: "The moto guy has the order and calls the client to find the exact spot.",
  delivered: "Settled in cash on delivery.",
  rejected: "The vendor declined this order.",
};

let renderGen = 0;

const state = {
  role: localStorage.getItem("desk.role") || "client",
  actor: localStorage.getItem("desk.actor") || "",
  vendorId: localStorage.getItem("desk.vendorId") || "",
  view: "home",
  menuVendorId: null,
  orderId: null,
  basket: {},
  neighborhood: "",
  stalls: [],
  signature: "",
  busy: false,
};

const main = document.getElementById("main");
const notice = document.getElementById("notice");
const summaryEl = document.getElementById("summary");
const actorInput = document.getElementById("actor");
const stallSelect = document.getElementById("stall");
const stallWrap = document.getElementById("stall-wrap");

actorInput.value = state.actor;

function money(cents) {
  return (cents / 100).toFixed(2);
}

function appendTo(node, child) {
  if (child == null || child === false) return;
  if (Array.isArray(child)) {
    child.forEach((item) => appendTo(node, item));
    return;
  }
  node.append(child.nodeType ? child : document.createTextNode(String(child)));
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (value == null || value === false) return;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value);
  });
  (children || []).forEach((child) => appendTo(node, child));
  return node;
}

function showError(error) {
  notice.hidden = false;
  notice.textContent = error.message || "Something went wrong.";
}

function clearError() {
  notice.hidden = true;
  notice.textContent = "";
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function sameName(a, b) {
  return String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();
}

function paintSummary(data) {
  const openLabel = data.open === 1 ? "1 open" : data.open + " open";
  const doneLabel = data.delivered === 1 ? "1 delivered" : data.delivered + " delivered";
  summaryEl.textContent = openLabel + " · " + doneLabel;
}

async function refreshSummary() {
  try {
    paintSummary(await api("/api/summary"));
  } catch (error) {
    summaryEl.textContent = "Desk is not responding.";
  }
}

function drawRoles() {
  const wrap = document.getElementById("roles");
  wrap.replaceChildren(
    ...ROLES.map(([id, label]) =>
      el("button", {
        type: "button",
        role: "tab",
        "aria-selected": state.role === id ? "true" : "false",
        onclick: () => setRole(id),
      }, [label])
    )
  );
  stallWrap.hidden = state.role !== "vendor";
}

function setRole(role) {
  state.role = role;
  state.view = "home";
  state.orderId = null;
  state.signature = "";
  localStorage.setItem("desk.role", role);
  drawRoles();
  render();
}

function requireActor() {
  const name = actorInput.value.trim();
  if (!name) {
    showError(new Error("Type your name first."));
    actorInput.focus();
    return "";
  }
  clearError();
  return name;
}

actorInput.addEventListener("input", () => {
  state.actor = actorInput.value;
  localStorage.setItem("desk.actor", state.actor);
});

stallSelect.addEventListener("change", () => {
  state.vendorId = stallSelect.value;
  localStorage.setItem("desk.vendorId", state.vendorId);
  state.signature = "";
  if (state.role === "vendor" && state.view === "home") render();
});

async function loadStalls() {
  state.stalls = await api("/api/vendors");
  if (!state.vendorId && state.stalls[0]) state.vendorId = String(state.stalls[0].id);
  stallSelect.replaceChildren(
    ...state.stalls.map((stall) =>
      el("option", { value: String(stall.id) }, [stall.name])
    )
  );
  stallSelect.value = state.vendorId;
}

function setMain(node, signature) {
  if (signature === state.signature) return;
  state.signature = signature;
  main.replaceChildren(node);
}

function statusPill(status) {
  return el("span", { class: "pill " + status }, [STATUS_LABEL[status] || status]);
}

function orderCard(order, onOpen) {
  return el("article", { class: "card" }, [
    el("button", { class: "block", type: "button", onclick: onOpen }, [
      el("div", { class: "row" }, [
        el("h3", {}, ["#" + order.id + " " + order.vendor_name]),
        statusPill(order.status),
      ]),
      el("p", { class: "quiet" }, [
        order.client_name + " · " + order.neighborhood + " · " + money(order.total_cents),
      ]),
    ]),
  ]);
}

function section(title, orders) {
  const block = el("section", { class: "stack" }, [
    el("p", { class: "section-label" }, [title]),
  ]);
  if (!orders.length) {
    block.append(el("p", { class: "empty card" }, ["Nothing here."]));
    return block;
  }
  orders.forEach((order) => {
    block.append(orderCard(order, () => openOrder(order.id)));
  });
  return block;
}

async function renderHome(gen) {
  if (state.role === "client") {
    const orders = state.actor.trim()
      ? await api("/api/orders?client_name=" + encodeURIComponent(state.actor.trim()))
      : [];
    const page = el("div", { class: "stack" }, [
      el("p", { class: "section-label" }, ["Stalls"]),
    ]);
    state.stalls.forEach((stall) => {
      page.append(el("article", { class: "card" }, [
        el("button", {
          class: "block",
          type: "button",
          onclick: () => openMenu(stall.id),
        }, [
          el("h2", {}, [stall.name]),
          el("p", { class: "quiet" }, [stall.description]),
        ]),
      ]));
    });
    page.append(section("Your orders", orders));
    if (gen !== renderGen) return;
    setMain(page, "client:" + state.actor + ":" + JSON.stringify(orders.map(signatureOrder)));
    return;
  }

  if (state.role === "vendor") {
    const stall = state.stalls.find((item) => String(item.id) === String(state.vendorId));
    const orders = stall ? await api("/api/orders?vendor_id=" + stall.id) : [];
    const page = el("div", { class: "stack" }, [
      el("h2", {}, [stall ? stall.name : "Pick a stall"]),
      section("Needs you", orders.filter((order) => order.status === "placed" || order.status === "accepted")),
      section("With the moto guy", orders.filter((order) => ["ready", "claimed", "picked_up"].includes(order.status))),
      section("Closed", orders.filter((order) => order.status === "delivered" || order.status === "rejected")),
    ]);
    if (gen !== renderGen) return;
    setMain(page, "vendor:" + state.vendorId + ":" + JSON.stringify(orders.map(signatureOrder)));
    return;
  }

  const ready = await api("/api/orders?status=ready");
  const mineName = state.actor.trim();
  const mine = mineName
    ? (await api("/api/orders?courier_name=" + encodeURIComponent(mineName))).filter((order) =>
        order.status === "claimed" || order.status === "picked_up"
      )
    : [];
  const page = el("div", { class: "stack" }, [
    section("Ready for pickup", ready),
    section("Yours", mine),
  ]);
  if (gen !== renderGen) return;
  setMain(page, "courier:" + mineName + ":" + JSON.stringify(ready.concat(mine).map(signatureOrder)));
}

function signatureOrder(order) {
  return [order.id, order.status, order.courier_name, order.total_cents];
}

function openMenu(vendorId) {
  state.view = "menu";
  state.menuVendorId = vendorId;
  state.basket = {};
  state.signature = "";
  render();
}

function openOrder(orderId) {
  state.view = "order";
  state.orderId = orderId;
  state.signature = "";
  render();
}

function renderMenu() {
  const stall = state.stalls.find((item) => item.id === state.menuVendorId);
  if (!stall) {
    state.view = "home";
    render();
    return;
  }
  const page = el("div", { class: "stack" }, [
    el("button", { class: "btn", type: "button", onclick: () => { state.view = "home"; state.signature = ""; render(); } }, ["Back"]),
    el("h2", {}, [stall.name]),
    el("p", { class: "quiet" }, [stall.description]),
  ]);
  stall.items.forEach((item) => {
    const qty = state.basket[item.id] || 0;
    page.append(el("article", { class: "card row" }, [
      el("div", {}, [
        el("h3", {}, [item.name]),
        el("p", { class: "quiet price" }, [money(item.price_cents)]),
      ]),
      el("div", { class: "qty" }, [
        el("button", { type: "button", onclick: () => changeQty(item.id, -1) }, ["−"]),
        el("span", {}, [String(qty)]),
        el("button", { type: "button", onclick: () => changeQty(item.id, 1) }, ["+"]),
      ]),
    ]));
  });
  const neighborhood = el("input", {
    id: "neighborhood",
    maxlength: "80",
    placeholder: "Neighborhood",
    value: state.neighborhood,
    oninput: (event) => { state.neighborhood = event.target.value; },
  });
  page.append(el("label", { class: "field" }, ["Neighborhood", neighborhood]));
  page.append(el("p", { class: "quiet" }, [
    "The moto guy calls you to narrow down the exact spot.",
  ]));
  const count = Object.values(state.basket).reduce((sum, qty) => sum + qty, 0);
  const total = stall.items.reduce(
    (sum, item) => sum + item.price_cents * (state.basket[item.id] || 0),
    0
  );
  page.append(el("div", { class: "basket" }, [
    el("div", {}, [
      el("strong", {}, [count ? count + " items · " + money(total) : "Basket is empty"]),
    ]),
    el("button", { class: "btn primary", type: "button", onclick: () => placeOrder(stall) }, ["Place order"]),
  ]));
  setMain(page, "menu:" + stall.id + ":" + JSON.stringify(state.basket) + ":" + state.neighborhood);
}

function changeQty(itemId, delta) {
  const next = (state.basket[itemId] || 0) + delta;
  if (next <= 0) delete state.basket[itemId];
  else state.basket[itemId] = Math.min(20, next);
  state.signature = "";
  renderMenu();
}

async function placeOrder(stall) {
  const name = requireActor();
  if (!name) return;
  const lines = Object.entries(state.basket)
    .filter(([, qty]) => qty > 0)
    .map(([itemId, qty]) => ({ item_id: Number(itemId), qty }));
  if (!lines.length) {
    showError(new Error("Add at least one item."));
    return;
  }
  if (!state.neighborhood.trim()) {
    showError(new Error("Say which neighborhood."));
    return;
  }
  try {
    clearError();
    const order = await api("/api/orders", {
      method: "POST",
      body: JSON.stringify({
        client_name: name,
        neighborhood: state.neighborhood.trim(),
        vendor_id: stall.id,
        lines,
      }),
    });
    state.basket = {};
    state.view = "order";
    state.orderId = order.id;
    state.signature = "";
    await render();
  } catch (error) {
    showError(error);
  }
}

function renderOrder(order) {
  const items = el("ul", { class: "timeline" });
  order.items.forEach((item) => {
    items.append(el("li", {}, [
      el("span", {}, [item.qty + " ×"]),
      el("span", {}, [item.name + " · " + money(item.unit_price_cents * item.qty)]),
    ]));
  });
  const timeline = el("ol", { class: "timeline" });
  order.events.forEach((event) => {
    timeline.append(el("li", {}, [
      el("span", {}, [event.created_at.slice(11, 16)]),
      el("span", {}, [event.note + " · " + event.actor]),
    ]));
  });
  const page = el("div", { class: "stack" }, [
    el("button", {
      class: "btn",
      type: "button",
      onclick: () => { state.view = "home"; state.orderId = null; state.signature = ""; render(); },
    }, ["Back"]),
    el("article", { class: "card stack" }, [
      el("div", { class: "row" }, [
        el("h2", {}, ["#" + order.id + " " + order.vendor_name]),
        statusPill(order.status),
      ]),
      el("p", {}, [order.client_name + " · " + order.neighborhood]),
      el("p", { class: "quiet" }, [
        "The moto guy calls the client to find the exact spot.",
      ]),
      el("p", { class: "quiet" }, [
        order.courier_name ? "Moto guy " + order.courier_name : "No moto guy yet",
      ]),
      el("p", {}, [NEXT_LINE[order.status] || ""]),
      order.settlement
        ? el("p", {}, ["Cash on delivery: " + money(order.total_cents)])
        : el("p", { class: "price" }, ["Total " + money(order.total_cents)]),
      items,
      el("div", { class: "actions" }, actionButtons(order)),
      el("p", { class: "section-label" }, ["Timeline"]),
      timeline,
    ]),
  ]);
  setMain(page, "order:" + JSON.stringify(order));
}

function actionButtons(order) {
  const name = state.actor.trim();
  const buttons = [];
  const add = (label, to, className) => {
    buttons.push(el("button", {
      class: "btn " + (className || "primary"),
      type: "button",
      onclick: () => move(order.id, to),
    }, [label]));
  };

  if (state.role === "vendor" && String(order.vendor_id) === String(state.vendorId)) {
    if (order.status === "placed") {
      add("Accept", "accepted");
      add("Reject", "rejected", "danger");
    } else if (order.status === "accepted") {
      add("Mark ready", "ready");
    }
  }
  if (state.role === "courier") {
    const mine = sameName(order.courier_name, name);
    if (order.status === "ready") add("Claim", "claimed");
    if (order.status === "claimed" && mine) {
      add("Picked up", "picked_up");
      add("Release", "ready", "danger");
    }
    if (order.status === "picked_up" && mine) add("Delivered", "delivered");
  }
  return buttons;
}

async function move(orderId, to) {
  const name = requireActor();
  if (!name || state.busy) return;
  state.busy = true;
  try {
    clearError();
    await api("/api/orders/" + orderId + "/transition", {
      method: "POST",
      body: JSON.stringify({ to, actor: name }),
    });
    state.signature = "";
    await render();
  } catch (error) {
    showError(error);
  } finally {
    state.busy = false;
  }
}

async function render() {
  const gen = ++renderGen;
  try {
    await refreshSummary();
    if (gen !== renderGen) return;
    if (state.view === "menu") {
      renderMenu();
      return;
    }
    if (state.view === "order" && state.orderId) {
      const order = await api("/api/orders/" + state.orderId);
      if (gen !== renderGen) return;
      renderOrder(order);
      return;
    }
    state.view = "home";
    await renderHome(gen);
  } catch (error) {
    if (gen === renderGen) showError(error);
  }
}

async function tick() {
  if (state.view === "menu" || state.busy) return;
  await render();
}

drawRoles();
loadStalls()
  .then(render)
  .catch(showError);
setInterval(tick, 3000);
