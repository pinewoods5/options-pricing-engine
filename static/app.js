/* Convexity — frontend.

   No framework. A DOM helper, one state object, and a render pass that
   rebuilds the three columns. Everything a user types goes through text nodes
   rather than innerHTML, so a ticker typed as markup is displayed, not run.

   Two timings shape the whole file. Pricing takes about a tenth of a second,
   so the ticket re-prices on a short debounce and the result feels live. The
   AI read takes seconds, so it is fired on a much longer debounce -- only once
   the position has actually settled -- and streams into a card below numbers
   that are already on screen. The page never waits on the model. */

const root = document.getElementById("app");

/* ---------- dom helper ---------- */

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "svg") node.innerHTML = v; // our own inline icons only
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "value") node.value = v;
    else node.setAttribute(k, v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/* ---------- icons ---------- */

const ICONS = {
  mark:
    '<svg width="34" height="34" viewBox="0 0 34 34" fill="none">' +
    '<path d="M4 26C9 26 11 8 17 8s8 18 13 18" stroke="#00c896" stroke-width="2.6" ' +
    'stroke-linecap="round"/><circle cx="17" cy="8" r="2.6" fill="#00c896"/></svg>',
  analyze:
    '<svg viewBox="0 0 24 24"><path d="M3 17l5-6 4 3 4-7 5 5" stroke-linecap="round" ' +
    'stroke-linejoin="round"/><circle cx="8" cy="11" r="1.3" fill="currentColor" stroke="none"/></svg>',
  watchlist:
    '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h9" stroke-linecap="round"/></svg>',
  history:
    '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/>' +
    '<path d="M12 7.5V12l3 2" stroke-linecap="round"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke-linecap="round"/></svg>',
  check:
    '<svg viewBox="0 0 20 20"><path d="M10 1.6l2.1 1.5 2.5-.3 1.1 2.3 2.2 1.3-.5 2.5.5 2.5-2.2 1.3' +
    '-1.1 2.3-2.5-.3L10 18.4l-2.1-1.5-2.5.3-1.1-2.3L2.1 13.6l.5-2.5-.5-2.5 2.2-1.3 1.1-2.3 2.5.3z"/>' +
    '<path d="M6.6 10.2l2.3 2.3 4.4-4.7" stroke="#0b1a12" stroke-width="1.8" fill="none" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>',
  warn:
    '<svg viewBox="0 0 20 20"><path d="M10 2.2l8 14.6H2z"/>' +
    '<path d="M10 8v4M10 14.3v.2" stroke="#1a1405" stroke-width="1.7" stroke-linecap="round"/></svg>',
};

/* ---------- state ---------- */

const state = {
  view: "analyze",
  structure: {
    name: "Long call",
    underlying: "ACME",
    spot: 100,
    rate: 0.05,
    vol: 0.25,
    time: 0.5,
    dividend: 0,
    style: "european",
    legs: [{ option_type: "call", strike: 100, quantity: 1 }],
  },
  analysis: null,
  analyzing: false,
  error: null,
  tab: "payoff",
  ticketOpen: false,
  modalOpen: false,
  read: null,
  readStatus: null,
  readError: null,
  readAvailable: false,
  pinned: null,
  showMatrix: false,
  glossary: null,
  templates: [],
  history: [],
  hover: null,
  readFor: null, // fingerprint the current read belongs to
};

/* ---------- formatting ----------
   One place, because a figure that appears in the table, the ticket footer and
   the chart readout has to look identical in all three. */

const fmt = {
  money: (v, d = 2) => {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    // A value that rounds to zero is zero. Without this, a gamma of -4e-19
    // prints as "-0.00000", which reads as a sign rather than as rounding.
    const rounded = Math.abs(v) < 0.5 / Math.pow(10, d) ? 0 : v;
    return rounded.toLocaleString("en-US", {
      minimumFractionDigits: d,
      maximumFractionDigits: d,
    });
  },
  signed: (v, d = 2) => (v > 0 ? "+" : "") + fmt.money(v, d),
  pct: (v, d = 1) => `${(v * 100).toFixed(d)}%`,
  metric(value, metric) {
    const display = (state.analysis && state.analysis.display[metric]) || { scale: 1, decimals: 2 };
    return fmt.money(value * display.scale, display.decimals);
  },
};

function unbounded(value, text) {
  return value === null || value === undefined ? text : fmt.money(value);
}

/* ---------- api ---------- */

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      detail = (await response.json()).detail || detail;
    } catch (e) {
      /* a non-JSON error body is still worth showing as a status code */
    }
    throw new Error(detail);
  }
  return response.json();
}

let analyzeTimer = null;
let readTimer = null;
let analyzeToken = 0;

function scheduleAnalyze(delay = 160) {
  clearTimeout(analyzeTimer);
  analyzeTimer = setTimeout(runAnalyze, delay);
}

async function runAnalyze() {
  const token = ++analyzeToken;
  state.analyzing = true;
  state.error = null;
  render();
  try {
    const analysis = await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify(state.structure),
    });
    // A slower earlier request must not overwrite a newer result.
    if (token !== analyzeToken) return;
    state.analysis = analysis;
    state.analyzing = false;

    // The read belongs to a fingerprint. If the position moved far enough to
    // change it, what is on screen is about a different position -- drop it.
    if (state.readFor && state.readFor !== analysis.structure.fingerprint) {
      state.read = null;
      state.readFor = null;
      state.readError = null;
    }
    render();
    scheduleRead();
  } catch (error) {
    if (token !== analyzeToken) return;
    state.analyzing = false;
    state.error = error.message;
    render();
  }
}

/* The read is fired only once the position has stopped moving. A slider drag
   would otherwise spend money on every intermediate position it passes
   through, none of which the user is looking at. */
function scheduleRead() {
  clearTimeout(readTimer);
  readTimer = setTimeout(runRead, 700);
}

async function runRead() {
  if (!state.analysis) return;
  const fingerprint = state.analysis.structure.fingerprint;
  if (state.readFor === fingerprint && state.read) return;

  state.readError = null;
  state.readStatus = "Reading the position…";
  state.read = null;
  render();

  let response;
  try {
    response = await fetch("/api/read", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.structure),
    });
  } catch (error) {
    state.readStatus = null;
    state.readError = error.message;
    return render();
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Server-sent events are separated by a blank line; the last chunk may be
    // a partial frame, so it stays in the buffer.
    const frames = buffer.split("\n\n");
    buffer = frames.pop();

    for (const frame of frames) {
      const eventLine = frame.match(/^event: (.+)$/m);
      const dataLine = frame.match(/^data: (.+)$/m);
      if (!eventLine || !dataLine) continue;
      const payload = JSON.parse(dataLine[1]);

      if (eventLine[1] === "status") {
        state.readStatus = payload.text;
      } else if (eventLine[1] === "result") {
        state.read = payload.read;
        state.readFor = fingerprint;
        state.readStatus = null;
      } else if (eventLine[1] === "error") {
        state.readStatus = null;
        state.readError = payload.message;
        if (payload.unavailable) state.readAvailable = false;
      }
      render();
    }
  }
  state.readStatus = null;
  render();
}

/* ---------- context panel ---------- */

function pin(title, body, unit) {
  state.pinned = { title, body, unit };
  state.showMatrix = false;
  render();
}

/* ---------- left rail ---------- */

function rail() {
  const item = (view, icon, label) =>
    h("button", {
      class: `rail-btn${state.view === view ? " active" : ""}`,
      title: label,
      "aria-label": label,
      onclick: () => {
        state.view = view;
        render();
      },
      svg: ICONS[icon],
    });

  return h(
    "nav",
    { class: "rail" },
    h("div", { class: "mark", title: "Convexity", svg: ICONS.mark }),
    item("analyze", "analyze", "Analyze"),
    item("watchlist", "watchlist", "Watchlist"),
    item("history", "history", "History"),
    h("div", { class: "rail-spacer" }),
    h("button", {
      class: "compose",
      title: "Analyze a new structure",
      "aria-label": "Analyze a new structure",
      onclick: () => {
        state.modalOpen = true;
        render();
      },
      svg: ICONS.plus,
    })
  );
}

/* ---------- the agreement badge ---------- */

function badge() {
  const validation = state.analysis && state.analysis.validation;
  if (!validation) return h("span", { class: "badge muted" }, "Pricing…");

  const dots = h(
    "span",
    { class: "badge-dots" },
    [0, 1, 2].map((i) =>
      h("span", { class: `badge-dot${i < validation.models_agreeing ? " on" : ""}` })
    )
  );

  return h(
    "button",
    {
      class: `badge ${validation.status}`,
      title: "How the three models compare, metric by metric",
      onclick: () => {
        state.showMatrix = !state.showMatrix;
        state.pinned = null;
        render();
      },
    },
    h("span", { svg: validation.status === "agree" ? ICONS.check : ICONS.warn }),
    validation.headline,
    dots
  );
}

/* ---------- chart tabs ---------- */

const TABS = [
  { key: "payoff", label: "Payoff" },
  { key: "greeks", label: "Greek profile" },
  { key: "vol", label: "Volatility" },
];

function tabBar() {
  const bar = h(
    "div",
    { class: "tabs" },
    TABS.map((tab) =>
      h(
        "button",
        {
          class: `tab${state.tab === tab.key ? " active" : ""}`,
          "data-tab": tab.key,
          onclick: () => {
            state.tab = tab.key;
            state.hover = null;
            render();
          },
        },
        tab.label
      )
    ),
    h("div", { class: "tab-indicator" })
  );

  // Positioned after layout so the indicator can slide between real widths.
  requestAnimationFrame(() => {
    const active = bar.querySelector(`.tab[data-tab="${state.tab}"]`);
    const indicator = bar.querySelector(".tab-indicator");
    if (!active || !indicator) return;
    indicator.style.left = `${active.offsetLeft}px`;
    indicator.style.width = `${active.offsetWidth}px`;
  });

  return bar;
}

function readout(items) {
  return h(
    "div",
    { class: "readout" },
    items.map(([label, value]) => h("span", {}, `${label} `, h("b", {}, value)))
  );
}

function chartPanel() {
  const analysis = state.analysis;
  if (!analysis) return h("div", { class: "chart-wrap" });

  const spot = analysis.structure.spot;
  const wrap = h("div", { class: "chart-wrap" });
  const readoutSlot = h("div", { class: "readout" });

  if (state.tab === "payoff") {
    const payoff = analysis.payoff;
    const markers = [{ x: spot, label: "spot" }].concat(
      payoff.breakevens.map((b) => ({ x: b, label: `break even ${fmt.money(b)}` }))
    );
    wrap.append(
      Charts.line({
        xs: payoff.spots,
        series: [
          {
            key: "profit",
            values: payoff.profits,
            color: "var(--accent)",
            area: true,
            splitAtZero: true,
          },
        ],
        height: 250,
        zeroLine: true,
        markers,
        xFormat: (v) => fmt.money(v, 0),
        yFormat: (v) => fmt.money(v, 0),
        onHover: (i) => {
          readoutSlot.replaceChildren();
          if (i === null) return;
          const profit = payoff.profits[i];
          readoutSlot.append(
            h("span", {}, "at ", h("b", {}, fmt.money(payoff.spots[i]))),
            h(
              "span",
              { class: profit >= 0 ? "up" : "down" },
              "profit ",
              h("b", {}, fmt.signed(profit))
            )
          );
        },
      }),
      h(
        "div",
        { class: "legend" },
        h("span", {}, h("i", { style: "background:var(--up)" }), "profit at expiry"),
        h("span", {}, h("i", { style: "background:var(--down)" }), "loss at expiry")
      ),
      readoutSlot
    );
  } else if (state.tab === "greeks") {
    const profile = analysis.spot_profile;
    wrap.append(
      Charts.line({
        xs: profile.spots,
        series: [
          { key: "delta", values: profile.delta, color: "var(--accent)" },
          { key: "gamma", values: profile.gamma.map((g) => g * 10), color: "#7aa2ff" },
        ],
        height: 250,
        zeroLine: true,
        markers: [{ x: spot, label: "spot" }],
        xFormat: (v) => fmt.money(v, 0),
        yFormat: (v) => v.toFixed(2),
        onHover: (i) => {
          readoutSlot.replaceChildren();
          if (i === null) return;
          readoutSlot.append(
            h("span", {}, "at ", h("b", {}, fmt.money(profile.spots[i]))),
            h("span", {}, "delta ", h("b", {}, profile.delta[i].toFixed(4))),
            h("span", {}, "gamma ", h("b", {}, profile.gamma[i].toFixed(5)))
          );
        },
      }),
      h(
        "div",
        { class: "legend" },
        h("span", {}, h("i", { style: "background:var(--accent)" }), "delta"),
        h("span", {}, h("i", { style: "background:#7aa2ff" }), "gamma (×10, to share the axis)")
      ),
      readoutSlot
    );
  } else {
    const profile = analysis.vol_profile;
    wrap.append(
      Charts.line({
        xs: profile.vols,
        series: [
          { key: "value", values: profile.price, color: "var(--accent)", area: true },
        ],
        height: 250,
        zeroLine: true,
        markers: [{ x: analysis.structure.vol, label: "today's input" }],
        xFormat: (v) => `${(v * 100).toFixed(0)}%`,
        yFormat: (v) => fmt.money(v, 0),
        onHover: (i) => {
          readoutSlot.replaceChildren();
          if (i === null) return;
          const change = profile.price[i] - analysis.position.price;
          readoutSlot.append(
            h("span", {}, "at ", h("b", {}, fmt.pct(profile.vols[i]))),
            h("span", {}, "worth ", h("b", {}, fmt.money(profile.price[i]))),
            h(
              "span",
              { class: change >= 0 ? "up" : "down" },
              "change ",
              h("b", {}, fmt.signed(change))
            )
          );
        },
      }),
      h(
        "div",
        { class: "chart-note" },
        "What this position is worth across a range of implied volatilities, " +
          "holding everything else still."
      ),
      readoutSlot
    );
  }

  return wrap;
}

/* ---------- cross-validation table ---------- */

function validationTable() {
  const analysis = state.analysis;
  const validation = analysis.validation;

  const head = h(
    "tr",
    {},
    h("th", {}, "Metric"),
    h("th", {}, "Black-Scholes"),
    h("th", {}, "Binomial tree"),
    h("th", {}, "Monte Carlo")
  );

  const rows = validation.rows.map((row) => {
    const cells = row.cells.map((cell) =>
      h(
        "td",
        { class: `num${cell.agrees ? "" : " cell-bad"}`, title: cell.basis },
        fmt.metric(cell.value, row.metric),
        cell.error
          ? h(
              "div",
              { class: "cell-err" },
              `± ${fmt.metric(cell.error * 1.96, row.metric)}`
            )
          : null
      )
    );
    return h(
      "tr",
      {},
      h(
        "td",
        {
          class: "metric-name",
          title: "What this means",
          onclick: () => explainMetric(row.metric, row.label),
        },
        row.label
      ),
      h("td", { class: "num" }, fmt.metric(row.reference, row.metric)),
      ...cells
    );
  });

  return h(
    "div",
    { class: "block" },
    h("div", { class: "block-title" }, "Three models, same position"),
    h("table", { class: "data" }, h("thead", {}, head), h("tbody", {}, rows)),
    validation.notes.map((note) =>
      h("div", { class: "notice", style: "margin-top:14px" }, note)
    )
  );
}

function explainMetric(metric, label) {
  const glossary = state.glossary || { greeks: {}, display: {} };
  const display = state.analysis.display[metric] || {};
  const body =
    metric === "price"
      ? "What this position costs to open, or pays you if it is a credit. " +
        "Three independent models agree on it before it is shown."
      : glossary.greeks[metric] || "";
  pin(label, body, display.per);
}

/* ---------- position summary ---------- */

function positionBlock() {
  const analysis = state.analysis;
  const payoff = analysis.payoff;
  const position = analysis.position;

  const stat = (key, value, className, onclick) =>
    h(
      "div",
      { class: `stat${onclick ? " clickable" : ""}`, onclick },
      h("div", { class: "k" }, key),
      h("div", { class: `v ${className || ""}` }, value)
    );

  const greekStats = ["delta", "gamma", "vega", "theta"].map((metric) =>
    stat(
      analysis.display[metric].label,
      fmt.metric(position[metric], metric),
      null,
      () => explainMetric(metric, analysis.display[metric].label)
    )
  );

  return [
    h(
      "div",
      { class: "block" },
      h("div", { class: "block-title" }, payoff.is_credit ? "Credit received" : "Cost to open"),
      h(
        "div",
        { class: "stat-row" },
        stat(
          payoff.is_credit ? "You receive" : "You pay",
          fmt.money(Math.abs(payoff.net_cost)),
          payoff.is_credit ? "up" : null
        ),
        stat("Best case", unbounded(payoff.max_profit, "Unlimited"), "up"),
        stat("Worst case", unbounded(payoff.max_loss, "Unlimited"), "down"),
        stat(
          "Breaks even at",
          payoff.breakevens.length
            ? payoff.breakevens.map((b) => fmt.money(b)).join("  ·  ")
            : "never"
        )
      )
    ),
    h(
      "div",
      { class: "block" },
      h("div", { class: "block-title" }, "Position greeks"),
      h("div", { class: "stat-row" }, greekStats)
    ),
  ];
}

/* ---------- the AI read card ---------- */

function readCard() {
  const body = [];

  if (state.readError) {
    body.push(h("div", { class: "notice" }, state.readError));
  } else if (state.readStatus) {
    body.push(
      h(
        "div",
        { class: "status-line" },
        h("span", { class: "dots" }, h("i"), h("i"), h("i")),
        state.readStatus
      )
    );
  } else if (state.read) {
    const read = state.read;
    body.push(
      h("h3", {}, read.headline),
      h("p", {}, read.position_summary),
      h(
        "p",
        {},
        h(
          "span",
          { class: `exposure ${read.volatility.exposure}` },
          read.volatility.exposure.replace(/_/g, " ")
        )
      ),
      h("p", {}, read.volatility.reading),
      h(
        "div",
        { class: "stat-row", style: "margin-bottom:14px" },
        h(
          "div",
          { class: "stat" },
          h("div", { class: "k" }, "If volatility rises"),
          h("div", { style: "font-size:13.5px;margin-top:3px" }, read.volatility.if_vol_rises)
        ),
        h(
          "div",
          { class: "stat" },
          h("div", { class: "k" }, "If volatility falls"),
          h("div", { style: "font-size:13.5px;margin-top:3px" }, read.volatility.if_vol_falls)
        )
      ),
      h("div", { class: "block-title", style: "margin-top:4px" }, "What this is assuming"),
      read.fragile_assumptions.map((item) =>
        h(
          "div",
          { class: "assumption" },
          h(
            "div",
            { class: "a-head" },
            h("span", { class: `sev ${item.severity}` }, item.severity),
            h("span", { class: "a-title" }, item.assumption)
          ),
          h("div", { class: "muted", style: "font-size:13.5px;margin-top:5px" }, item.why_it_matters),
          h(
            "div",
            { style: "font-size:13.5px;margin-top:5px" },
            h("span", { class: "faint" }, "Breaks if: "),
            item.what_would_break_it
          )
        )
      ),
      h("div", { class: "block-title", style: "margin-top:18px" }, "Worth watching"),
      h("ul", { class: "watch" }, read.watch_items.map((item) => h("li", {}, item)))
    );
  } else if (!state.readAvailable) {
    body.push(
      h(
        "div",
        { class: "notice" },
        "No API key is configured, so the volatility read is unavailable. " +
          "Everything else on this page — pricing, greeks, and the three-model " +
          "cross-check — works without one."
      )
    );
  } else {
    body.push(h("div", { class: "status-line" }, "Waiting for the position to settle…"));
  }

  return h(
    "div",
    { class: "read" },
    h(
      "div",
      { class: "read-head" },
      h("div", { class: "read-avatar" }, "C"),
      h("div", {}, h("div", { class: "read-title" }, "Volatility read"), h(
        "div",
        { class: "faint", style: "font-size:12px" },
        "Generated from the numbers above"
      ))
    ),
    body
  );
}

/* ---------- centre column ---------- */

function analyzeView() {
  if (state.error) {
    return h(
      "div",
      { class: "empty" },
      h("h2", {}, "That structure could not be priced"),
      h("p", {}, state.error),
      h("button", { class: "pill", onclick: () => (state.ticketOpen = true, render()) }, "Edit it")
    );
  }
  if (!state.analysis) {
    return h("div", { class: "empty" }, h("p", {}, "Pricing…"));
  }

  const structure = state.analysis.structure;
  return [
    h(
      "div",
      { class: "col-head" },
      h(
        "div",
        { class: "head-row" },
        h(
          "div",
          {},
          h("h1", {}, structure.name),
          h(
            "div",
            { class: "sub" },
            `${structure.underlying} · ${structure.legs.length} leg${
              structure.legs.length > 1 ? "s" : ""
            } · ${structure.days_to_expiry} days to expiry · ${fmt.pct(structure.vol)} vol`
          )
        ),
        badge()
      )
    ),
    tabBar(),
    chartPanel(),
    positionBlock(),
    validationTable(),
    readCard(),
  ];
}

function placeholderView(title, text) {
  return h(
    "div",
    { class: "empty" },
    h("h2", {}, title),
    h("p", {}, text),
    h(
      "button",
      { class: "pill ghost", onclick: () => ((state.view = "analyze"), render()) },
      "Back to Analyze"
    )
  );
}

function historyView() {
  if (!state.history.length) {
    return placeholderView(
      "Nothing here yet",
      "Positions you analyze are listed here so you can come back to them."
    );
  }
  return [
    h("div", { class: "col-head" }, h("h1", {}, "History")),
    h(
      "div",
      { class: "block" },
      h(
        "table",
        { class: "data" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            h("th", {}, "Structure"),
            h("th", {}, "Underlying"),
            h("th", {}, "Legs"),
            h("th", {}, "Value")
          )
        ),
        h(
          "tbody",
          {},
          state.history.map((row) =>
            h(
              "tr",
              {},
              h("td", { class: "metric-name" }, row.name),
              h("td", { class: "num" }, row.underlying),
              h("td", { class: "num" }, String(row.legs)),
              h("td", { class: "num" }, fmt.money(row.price))
            )
          )
        )
      )
    ),
  ];
}

function column() {
  let content;
  if (state.view === "analyze") content = analyzeView();
  else if (state.view === "history") content = historyView();
  else
    content = placeholderView(
      "Watchlist",
      "Somewhere to keep the underlyings you follow. Not built yet — this phase is " +
        "about getting one position analysed properly, end to end."
    );
  return h("main", { class: "column" }, content);
}

/* ---------- right context panel ---------- */

function contextPanel() {
  const children = [];

  if (state.showMatrix && state.analysis) {
    const validation = state.analysis.validation;
    children.push(
      h("h2", {}, "Model agreement"),
      h(
        "div",
        { class: "muted", style: "font-size:13.5px;margin-bottom:12px" },
        "Black-Scholes is the reference. The other two are checked against it, " +
          "metric by metric — Monte Carlo against its own error bar."
      ),
      validation.rows.map((row) =>
        h(
          "div",
          { class: "matrix-row" },
          row.cells.map((cell) =>
            h(
              "div",
              {},
              h(
                "div",
                { class: "m-head" },
                h("span", {}, `${row.label} · ${cell.label}`),
                h(
                  "span",
                  { class: cell.agrees ? "up" : "down" },
                  cell.agrees ? "agrees" : "differs"
                )
              ),
              h("div", { class: "m-basis" }, cell.basis)
            )
          )
        )
      )
    );
  } else if (state.pinned) {
    children.push(
      h("h2", {}, "In plain English"),
      h(
        "div",
        { class: "pinned" },
        h("div", { class: "p-title" }, state.pinned.title),
        h("div", { class: "p-body" }, state.pinned.body),
        state.pinned.unit ? h("div", { class: "p-unit" }, `Measured ${state.pinned.unit}.`) : null
      )
    );
  }

  const glossary = state.glossary;
  if (glossary) {
    children.push(
      h("h2", { style: children.length ? "margin-top:22px" : "" }, "The greeks"),
      Object.entries(glossary.greeks).map(([key, text]) =>
        h(
          "div",
          {
            class: "glossary-item",
            onclick: () =>
              pin(
                key.charAt(0).toUpperCase() + key.slice(1),
                text,
                (glossary.display[key] || {}).per
              ),
          },
          h("div", { class: "g-term" }, key.charAt(0).toUpperCase() + key.slice(1)),
          h("div", { class: "g-def" }, text)
        )
      )
    );
  }

  return h("aside", { class: "context" }, children);
}

/* ---------- the structure ticket ---------- */

function numberField(label, value, step, onchange, hint) {
  return h(
    "div",
    { class: "field" },
    h("label", {}, label),
    h("input", {
      type: "number",
      step,
      value,
      oninput: (event) => {
        const parsed = parseFloat(event.target.value);
        if (!isNaN(parsed)) {
          onchange(parsed);
          scheduleAnalyze();
        }
      },
    }),
    hint ? h("div", { class: "faint", style: "font-size:11.5px;margin-top:4px" }, hint) : null
  );
}

function ticket() {
  const structure = state.structure;

  const legRows = structure.legs.map((leg, index) =>
    h(
      "div",
      { class: "leg-row" },
      h(
        "button",
        {
          class: `side-btn ${leg.quantity > 0 ? "buy" : "sell"}`,
          title: "Switch between buying and selling this leg",
          onclick: () => {
            leg.quantity = -leg.quantity;
            scheduleAnalyze(0);
            render();
          },
        },
        leg.quantity > 0 ? "Buy" : "Sell"
      ),
      h(
        "select",
        {
          onchange: (event) => {
            leg.option_type = event.target.value;
            scheduleAnalyze(0);
            render();
          },
        },
        h("option", { value: "call", selected: leg.option_type === "call" }, "Call"),
        h("option", { value: "put", selected: leg.option_type === "put" }, "Put")
      ),
      h("input", {
        type: "number",
        step: "0.5",
        value: leg.strike,
        title: "Strike",
        oninput: (event) => {
          const parsed = parseFloat(event.target.value);
          if (!isNaN(parsed) && parsed > 0) {
            leg.strike = parsed;
            scheduleAnalyze();
          }
        },
      }),
      h("input", {
        type: "number",
        step: "1",
        min: "1",
        value: Math.abs(leg.quantity),
        title: "Contracts",
        oninput: (event) => {
          const parsed = parseInt(event.target.value, 10);
          if (!isNaN(parsed) && parsed > 0) {
            leg.quantity = Math.sign(leg.quantity) * parsed;
            scheduleAnalyze();
          }
        },
      }),
      structure.legs.length > 1
        ? h(
            "button",
            {
              class: "leg-del",
              title: "Remove this leg",
              onclick: () => {
                structure.legs.splice(index, 1);
                scheduleAnalyze(0);
                render();
              },
            },
            "×"
          )
        : h("span")
    )
  );

  const analysis = state.analysis;
  const price = analysis ? analysis.payoff.net_cost : null;
  const validation = analysis ? analysis.validation : null;

  return h(
    "aside",
    { class: "ticket" },
    h(
      "div",
      { class: "ticket-head" },
      h("h2", {}, "Structure"),
      h(
        "button",
        {
          class: "icon-btn",
          "aria-label": "Close",
          onclick: () => {
            state.ticketOpen = false;
            render();
          },
        },
        "×"
      )
    ),
    h(
      "div",
      { class: "ticket-body" },
      h(
        "div",
        { class: "field" },
        h("label", {}, "Name"),
        h("input", {
          type: "text",
          value: structure.name,
          oninput: (event) => {
            structure.name = event.target.value;
            scheduleAnalyze(400);
          },
        })
      ),
      h(
        "div",
        { class: "grid2" },
        h(
          "div",
          { class: "field" },
          h("label", {}, "Underlying"),
          h("input", {
            type: "text",
            value: structure.underlying,
            oninput: (event) => {
              structure.underlying = event.target.value;
              scheduleAnalyze(400);
            },
          })
        ),
        h(
          "div",
          { class: "field" },
          h("label", {}, "Exercise style"),
          h(
            "select",
            {
              onchange: (event) => {
                structure.style = event.target.value;
                scheduleAnalyze(0);
                render();
              },
            },
            h("option", { value: "european", selected: structure.style === "european" }, "European"),
            h("option", { value: "american", selected: structure.style === "american" }, "American")
          )
        )
      ),

      h("div", { class: "block-title", style: "margin:22px 0 0" }, "Legs"),
      legRows,
      h(
        "button",
        {
          class: "add-leg",
          disabled: structure.legs.length >= 4,
          onclick: () => {
            structure.legs.push({ option_type: "call", strike: structure.spot, quantity: 1 });
            scheduleAnalyze(0);
            render();
          },
        },
        structure.legs.length >= 4 ? "Four legs is the maximum this phase" : "+ Add a leg"
      ),

      h("div", { class: "block-title", style: "margin:24px 0 0" }, "Market"),
      h(
        "div",
        { class: "grid2" },
        numberField("Underlying price", structure.spot, "0.5", (v) => (structure.spot = v)),
        numberField(
          "Volatility",
          structure.vol,
          "0.01",
          (v) => (structure.vol = v),
          "As a decimal: 0.25 is 25%"
        )
      ),
      h(
        "div",
        { class: "grid3" },
        numberField("Rate", structure.rate, "0.005", (v) => (structure.rate = v)),
        numberField("Dividend", structure.dividend, "0.005", (v) => (structure.dividend = v)),
        numberField("Years to expiry", structure.time, "0.05", (v) => (structure.time = v))
      )
    ),
    h(
      "div",
      { class: "ticket-foot" },
      h(
        "div",
        { class: "foot-row" },
        h(
          "div",
          {},
          h("div", { class: "foot-k" }, price !== null && price < 0 ? "Credit received" : "Cost to open"),
          h(
            "div",
            { class: `foot-v ${price !== null && price < 0 ? "up" : ""}` },
            price === null ? "—" : fmt.money(Math.abs(price))
          ),
          h(
            "div",
            { class: "foot-sub" },
            state.analyzing
              ? "Repricing…"
              : validation
              ? `${validation.headline} · ${state.analysis.elapsed_ms} ms`
              : ""
          )
        ),
        h(
          "button",
          {
            class: "pill",
            onclick: () => {
              state.ticketOpen = false;
              render();
            },
          },
          "Done"
        )
      ),
      state.error ? h("div", { class: "error-text" }, state.error) : null
    )
  );
}

/* ---------- the new-structure modal ---------- */

function modal() {
  return [
    h("div", {
      class: "scrim",
      onclick: () => {
        state.modalOpen = false;
        render();
      },
    }),
    h(
      "div",
      { class: "modal", role: "dialog", "aria-label": "Analyze a new structure" },
      h(
        "div",
        { class: "modal-head" },
        h("h2", {}, "Analyze a new structure"),
        h(
          "button",
          {
            class: "icon-btn",
            "aria-label": "Close",
            onclick: () => {
              state.modalOpen = false;
              render();
            },
          },
          "×"
        )
      ),
      state.templates.map((template) =>
        h(
          "button",
          { class: "template", onclick: () => chooseTemplate(template) },
          h(
            "div",
            { class: "t-head" },
            h("span", { class: "t-name" }, template.name),
            h("span", { class: "t-outlook" }, template.outlook)
          ),
          h("div", { class: "t-summary" }, template.summary)
        )
      )
    ),
  ];
}

async function chooseTemplate(template) {
  const seeded = await api(`/api/templates/${template.key}?spot=${state.structure.spot}`, {
    method: "POST",
  });
  state.structure.name = seeded.name;
  state.structure.legs = seeded.legs.map((leg) => ({
    option_type: leg.option_type,
    strike: leg.strike,
    quantity: leg.quantity,
  }));
  state.modalOpen = false;
  state.ticketOpen = true;
  state.view = "analyze";
  render();
  runAnalyze();
}

/* ---------- render ---------- */

function render() {
  const children = [rail(), column(), contextPanel()];
  if (state.ticketOpen) {
    children.push(
      h("div", {
        class: "scrim",
        onclick: () => {
          state.ticketOpen = false;
          render();
        },
      }),
      ticket()
    );
  }
  if (state.modalOpen) children.push(...modal());
  root.replaceChildren(...children.flat(Infinity));
}

/* ---------- boot ---------- */

async function boot() {
  render();
  const [status, glossary, templates] = await Promise.all([
    api("/api/status"),
    api("/api/glossary"),
    api("/api/templates"),
  ]);
  state.readAvailable = status.read_available;
  state.glossary = glossary;
  state.templates = templates.templates;
  render();
  await runAnalyze();
  api("/api/history").then((data) => {
    state.history = data.history;
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.modalOpen) state.modalOpen = false;
  else if (state.ticketOpen) state.ticketOpen = false;
  else return;
  render();
});

boot();
