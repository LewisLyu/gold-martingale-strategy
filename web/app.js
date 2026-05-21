const $ = (selector) => document.querySelector(selector);
let isBound = false;
let exchanges = [];
let selectedExchange = "binance";

function currentExchange() {
  return exchanges.find((item) => item.id === selectedExchange) || exchanges[0];
}

function readForm() {
  const form = new FormData($("#configForm"));
  const payload = Object.fromEntries(form.entries());
  payload.exchange = selectedExchange;
  payload.apiKey = $("#apiKey").value.trim();
  payload.apiSecret = $("#apiSecret").value.trim();
  payload.passphrase = $("#passphrase").value.trim();
  payload.live = form.get("live") === "on";
  payload.testnet = form.get("testnet") === "on";
  payload.autoReopen = form.get("autoReopen") === "on";
  return payload;
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function setText(id, value, suffix = "") {
  $(id).textContent = value ? `${value}${suffix}` : "-";
}

function renderLadder(rows) {
  const body = $("#ladderRows");
  body.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.level}</td>
      <td>${row.drawdown}</td>
      <td>${row.buy}U</td>
      <td>${row.cumulative}U</td>
      <td>${row.value}U</td>
      <td>${row.loss}U</td>
    `;
    body.appendChild(tr);
  });

  const visual = $("#ladderVisual");
  visual.innerHTML = "";
  const maxBuy = Math.max(...rows.map((row) => Number(row.buy)));
  rows.forEach((row) => {
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${Math.max(8, (Number(row.buy) / maxBuy) * 230)}px`;
    bar.innerHTML = `<span>${row.level}</span>`;
    visual.appendChild(bar);
  });
}

function renderStatus(data) {
  const running = data.running;
  if (Array.isArray(data.exchanges) && data.exchanges.length) {
    exchanges = data.exchanges;
    if (!selectedExchange) selectedExchange = data.exchange || exchanges[0].id;
    renderExchangeSelect();
  }
  $("#runState").textContent = running ? "运行中" : "未运行";
  $("#runState").classList.toggle("running", running);
  $("#modeLabel").textContent = data.live ? "实盘模式" : "实盘准备";
  const exchange = exchanges.find((item) => item.id === (data.exchange || selectedExchange));
  $("#boundExchange").textContent = exchange ? exchange.name : data.exchange || "-";
  $("#adapterStatus").textContent = exchange ? exchange.note : "-";
  setText("#lastPrice", data.lastPrice);
  setText("#invested", data.invested, "U");
  setText("#floatingPnl", data.floatingPnl, "U");
  setText("#pressure", data.pressure, "U");
  setText("#addBreakeven", data.addBreakeven);
  setText("#coreTakeProfit", data.coreTakeProfit);
  setText("#stopPrice", data.stopPrice);
  $("#cycleLabel").textContent = `cycle ${data.state.cycle_id || 0}`;
  $("#lastAction").textContent = (data.lastActions || []).join(" / ") || data.state.last_action || "-";
  $("#errorBox").textContent = data.lastError || "";
  renderLadder(data.ladder || []);
}

function renderExchangeSelect() {
  const select = $("#exchangeSelect");
  if (!select) return;
  if (select.options.length !== exchanges.length) {
    select.innerHTML = "";
    exchanges.forEach((exchange) => {
      const option = document.createElement("option");
      option.value = exchange.id;
      option.textContent = exchange.name;
      select.appendChild(option);
    });
  }
  select.value = selectedExchange;
  updateExchangeUi();
}

function updateExchangeUi() {
  const exchange = currentExchange();
  if (!exchange) return;
  $("#passphraseWrap").classList.toggle("hidden", !exchange.needsPassphrase);
  $("#exchangeNote").textContent = exchange.note || "";
  const symbol = document.querySelector('input[name="symbol"]');
  if (symbol && !isBound) symbol.value = exchange.defaultSymbol || "XAUUSDT";
}

async function refresh() {
  const response = await fetch("/api/status");
  renderStatus(await response.json());
}

$("#startBtn").addEventListener("click", async () => {
  try {
    if (!isBound) throw new Error("请先绑定账户");
    $("#errorBox").textContent = "";
    await api("/api/start", readForm());
    await refresh();
  } catch (error) {
    $("#errorBox").textContent = error.message;
  }
});

$("#bindBtn").addEventListener("click", async () => {
  selectedExchange = $("#exchangeSelect").value || "binance";
  const apiKey = $("#apiKey").value.trim();
  const apiSecret = $("#apiSecret").value.trim();
  if (!apiKey || !apiSecret) {
    $("#authError").textContent = "请填写 API Key 和 Secret Key";
    return;
  }
  try {
    await api("/api/bind", {
      exchange: selectedExchange,
      apiKey,
      apiSecret,
      passphrase: $("#passphrase").value.trim(),
    });
  } catch (error) {
    $("#authError").textContent = error.message;
    return;
  }
  isBound = true;
  $("#apiKey").value = "";
  $("#apiSecret").value = "";
  $("#passphrase").value = "";
  $("#authError").textContent = "";
  document.body.classList.remove("locked");
  document.body.classList.add("bound");
  await refresh();
});

$("#changeAuthBtn").addEventListener("click", async () => {
  try {
    await api("/api/clear-auth");
  } catch (error) {
    $("#errorBox").textContent = error.message;
    return;
  }
  isBound = false;
  document.body.classList.add("locked");
  document.body.classList.remove("bound");
});

$("#exchangeSelect").addEventListener("change", (event) => {
  selectedExchange = event.target.value;
  updateExchangeUi();
});

$("#stopBtn").addEventListener("click", async () => {
  try {
    await api("/api/stop");
    await refresh();
  } catch (error) {
    $("#errorBox").textContent = error.message;
  }
});

$("#resetBtn").addEventListener("click", async () => {
  try {
    await api("/api/reset");
    await refresh();
  } catch (error) {
    $("#errorBox").textContent = error.message;
  }
});

$("#tickBtn").addEventListener("click", async () => {
  try {
    const payload = readForm();
    payload.price = $("#manualPrice").value;
    if (!payload.price) throw new Error("请输入手动价格");
    renderStatus(await api("/api/tick", payload));
  } catch (error) {
    $("#errorBox").textContent = error.message;
  }
});

$("#detailsToggle").addEventListener("click", () => {
  const panel = $("#detailsPanel");
  const hidden = panel.classList.toggle("collapsed");
  $("#detailsToggle").textContent = hidden ? "查看策略详情" : "隐藏策略详情";
});

$("#configForm").addEventListener("input", async (event) => {
  if (event.target.name === "apiKey" || event.target.name === "apiSecret") return;
  try {
    const payload = readForm();
    const response = await fetch("/api/status");
    const data = await response.json();
    data.config.initial_notional = payload.initialNotional;
    renderStatus(data);
  } catch {
    return;
  }
});

refresh();
setInterval(refresh, 3000);
