/**
 * GEX Dashboard — Frontend
 * TradingView Lightweight Charts + SocketIO real-time updates
 */

// ── State ──────────────────────────────────────────────────────────────────

let currentSymbol = "NIFTY";
let currentDate = new Date().toISOString().split("T")[0];
let currentTF = 1;  // 1, 5, or 15 minutes

// Charts
let priceChart, priceSeries;
let levelLines = {};  // name -> LineSeries
let priceLines = {};  // name -> PriceLine (for removal)
let gexChart, ceGexSeries, peGexSeries;
let tlChart, tlSeries;

// Level colors
const LEVEL_COLORS = {
    call_wall:  { color: "#3fb950", title: "Call Wall",   style: 2 },  // Dashed
    put_wall:   { color: "#f85149", title: "Put Wall",    style: 2 },
    peak_gamma: { color: "#bc8cff", title: "Peak Gamma",  style: 0 },  // Solid
    max_pain:   { color: "#f0883e", title: "Max Pain",    style: 0 },
    hvl:        { color: "#d29922", title: "HVL",         style: 2 },
    local_flip: { color: "#39d2c0", title: "Local Flip",  style: 2 },
    em_upper:   { color: "#2196F3", title: "EM Upper",    style: 3 },  // Dotted
    em_lower:   { color: "#2196F3", title: "EM Lower",    style: 3 },
};

// ── Socket ─────────────────────────────────────────────────────────────────

const socket = io();

socket.on("connect", () => {
    document.getElementById("status-dot").classList.add("live");
});

socket.on("disconnect", () => {
    document.getElementById("status-dot").classList.remove("live");
});

socket.on("gex_update", (data) => {
    if (data.symbol === currentSymbol) {
        updateSnapshot(data.snapshot);
        if (data.latest_candle) {
            addCandle(data.latest_candle);
        }
        document.getElementById("last-update").textContent =
            "Updated: " + new Date().toLocaleTimeString();
    }
});

socket.on("refresh_complete", (data) => {
    const btn = document.getElementById("btn-refresh");
    btn.disabled = false;
    btn.textContent = data.success ? "Refresh" : "Failed";
    if (!data.success) setTimeout(() => { btn.textContent = "Refresh"; }, 2000);
});

// ── Init Charts ────────────────────────────────────────────────────────────

function initPriceChart() {
    const container = document.getElementById("price-chart");
    priceChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: container.clientHeight || 600,
        layout: {
            background: { color: "#1c2128" },
            textColor: "#8b949e",
        },
        grid: {
            vertLines: { color: "#21262d" },
            horzLines: { color: "#21262d" },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: "#30363d",
        },
        timeScale: {
            borderColor: "#30363d",
            timeVisible: true,
            secondsVisible: false,
        },
    });

    priceSeries = priceChart.addCandlestickSeries({
        upColor: "#3fb950",
        downColor: "#f85149",
        borderDownColor: "#f85149",
        borderUpColor: "#3fb950",
        wickDownColor: "#f85149",
        wickUpColor: "#3fb950",
    });

    // Create level line series
    for (const [key, cfg] of Object.entries(LEVEL_COLORS)) {
        const isEM = key.startsWith("em_");
        levelLines[key] = priceChart.addLineSeries({
            color: isEM ? "rgba(33, 150, 243, 0.6)" : cfg.color,
            lineWidth: isEM ? 1 : 2,
            lineStyle: cfg.style != null ? cfg.style : LightweightCharts.LineStyle.Dashed,
            title: cfg.title,
            priceLineVisible: false,
            lastValueVisible: true,
            crosshairMarkerVisible: false,
        });
    }

    // Resize handler — track both width and height
    new ResizeObserver(() => {
        priceChart.applyOptions({
            width: container.clientWidth,
            height: container.clientHeight,
        });
    }).observe(container);
}

function initGexChart() {
    const container = document.getElementById("gex-chart");
    gexChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 350,
        layout: {
            background: { color: "#1c2128" },
            textColor: "#8b949e",
        },
        grid: {
            vertLines: { color: "#21262d" },
            horzLines: { color: "#21262d" },
        },
        rightPriceScale: {
            borderColor: "#30363d",
        },
        timeScale: {
            borderColor: "#30363d",
        },
    });

    ceGexSeries = gexChart.addHistogramSeries({
        color: "#3fb950",
        priceFormat: { type: "volume" },
        priceScaleId: "gex",
        title: "CE GEX",
    });

    peGexSeries = gexChart.addHistogramSeries({
        color: "#f85149",
        priceFormat: { type: "volume" },
        priceScaleId: "gex",
        title: "PE GEX",
    });

    new ResizeObserver(() => {
        gexChart.applyOptions({ width: container.clientWidth });
    }).observe(container);
}

function initTimelineChart() {
    const container = document.getElementById("gex-timeline");
    tlChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 250,
        layout: {
            background: { color: "#1c2128" },
            textColor: "#8b949e",
        },
        grid: {
            vertLines: { color: "#21262d" },
            horzLines: { color: "#21262d" },
        },
        rightPriceScale: { borderColor: "#30363d" },
        timeScale: {
            borderColor: "#30363d",
            timeVisible: true,
            secondsVisible: false,
        },
    });

    tlSeries = tlChart.addAreaSeries({
        topColor: "rgba(88, 166, 255, 0.3)",
        bottomColor: "rgba(88, 166, 255, 0.02)",
        lineColor: "#58a6ff",
        lineWidth: 2,
        title: "Net GEX",
        priceFormat: { type: "volume" },
    });

    new ResizeObserver(() => {
        tlChart.applyOptions({ width: container.clientWidth });
    }).observe(container);
}

// ── Data Loading ───────────────────────────────────────────────────────────

async function fetchJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) return null;
    return resp.json();
}

async function loadAll() {
    // Clear all series before loading new symbol/date
    priceSeries.setData([]);
    for (const key of Object.keys(levelLines)) {
        levelLines[key].setData([]);
    }
    tlSeries.setData([]);
    ceGexSeries.setData([]);
    peGexSeries.setData([]);

    const [prices, levels, snapshot, strikes, snapshots] = await Promise.all([
        fetchJSON(`/api/prices/${currentSymbol}?date=${currentDate}&tf=${currentTF}`),
        fetchJSON(`/api/levels/${currentSymbol}?date=${currentDate}`),
        fetchJSON(`/api/snapshot/${currentSymbol}`),
        fetchJSON(`/api/strikes/${currentSymbol}`),
        fetchJSON(`/api/snapshots/${currentSymbol}?date=${currentDate}`),
    ]);

    // Price chart
    if (prices && prices.length) {
        priceSeries.setData(prices);
    }

    // Level lines
    if (levels) {
        for (const [key, data] of Object.entries(levels)) {
            if (levelLines[key] && data.length) {
                levelLines[key].setData(data);
            }
        }
    }

    // Add price line markers for latest level values
    if (snapshot) {
        const levelMap = {
            call_wall: snapshot.call_wall,
            put_wall: snapshot.put_wall,
            peak_gamma: snapshot.peak_gamma,
            max_pain: snapshot.max_pain,
            hvl: snapshot.hvl,
            local_flip: snapshot.local_flip,
            em_upper: snapshot.em_upper,
            em_lower: snapshot.em_lower,
        };
        for (const [key, val] of Object.entries(levelMap)) {
            if (!levelLines[key]) continue;
            // Remove old price line
            if (priceLines[key]) {
                try { levelLines[key].removePriceLine(priceLines[key]); } catch(e) {}
            }
            if (val && val > 0) {
                priceLines[key] = levelLines[key].createPriceLine({
                    price: val,
                    color: LEVEL_COLORS[key].color,
                    lineWidth: 1,
                    lineStyle: LEVEL_COLORS[key].style != null ? LEVEL_COLORS[key].style : 2,
                    axisLabelVisible: true,
                    title: LEVEL_COLORS[key].title,
                });
            }
        }
    }

    // Auto-fit price chart to new data range
    priceChart.timeScale().fitContent();

    // Latest snapshot
    if (snapshot) {
        updateSnapshot(snapshot);
    }

    // Strike GEX bar chart
    if (strikes && strikes.length) {
        updateStrikeChart(strikes);
    }

    // Net GEX timeline
    if (snapshots && snapshots.length) {
        updateTimeline(snapshots);
    }
}

// ── Update Functions ───────────────────────────────────────────────────────

function updateSnapshot(snap) {
    // Spot price
    document.getElementById("spot-price").textContent =
        snap.spot ? snap.spot.toFixed(2) : "--";

    // Levels
    setLevel("lv-call-wall", snap.call_wall);
    setLevel("lv-put-wall", snap.put_wall);
    setLevel("lv-peak-gamma", snap.peak_gamma);
    setLevel("lv-max-pain", snap.max_pain);
    setLevel("lv-hvl", snap.hvl);
    setLevel("lv-local-flip", snap.local_flip);

    // EM range
    if (snap.em_upper && snap.em_lower) {
        document.getElementById("lv-em").textContent =
            `${snap.em_lower.toFixed(0)} — ${snap.em_upper.toFixed(0)}`;
    }
    if (snap.atm_iv || snap.dte) {
        document.getElementById("lv-iv-dte").textContent =
            `${(snap.atm_iv || 0).toFixed(1)}% / ${snap.dte || 0}d`;
    }

    // XAUUSD section (GOLD only)
    const xauSection = document.getElementById("xau-section");
    if (currentSymbol === "GOLD" && snap.xau_spot) {
        xauSection.style.display = "block";
        document.getElementById("xau-usdinr").textContent = (snap.usdinr || 0).toFixed(2);
        document.getElementById("xau-spot").textContent = "$" + snap.xau_spot.toFixed(2);
        setXauLevel("xau-call-wall", snap.xau_call_wall);
        setXauLevel("xau-put-wall", snap.xau_put_wall);
        setXauLevel("xau-peak-gamma", snap.xau_peak_gamma);
        setXauLevel("xau-max-pain", snap.xau_max_pain);
        setXauLevel("xau-local-flip", snap.xau_local_flip);
        if (snap.xau_em_upper && snap.xau_em_lower) {
            document.getElementById("xau-em").textContent =
                `$${snap.xau_em_lower.toFixed(2)} — $${snap.xau_em_upper.toFixed(2)}`;
        }
    } else {
        xauSection.style.display = "none";
    }

    // Bias
    const biasEl = document.getElementById("bias-badge");
    const bias = (snap.bias || "NEUTRAL").replace(" ", "_");
    biasEl.textContent = snap.bias || "NEUTRAL";
    biasEl.className = "bias-badge bias-" + bias;

    // Metrics
    document.getElementById("m-bias-score").textContent = snap.bias_score || "--";
    document.getElementById("m-gamma-cond").textContent = snap.gamma_condition || "--";
    document.getElementById("m-gamma-tilt").textContent =
        snap.gamma_tilt ? snap.gamma_tilt.toFixed(2) : "--";
    document.getElementById("m-regime").textContent = snap.regime || "--";

    const netGex = snap.net_gex || 0;
    const gexEl = document.getElementById("m-net-gex");
    gexEl.textContent = formatGex(netGex);
    gexEl.className = "value " + (netGex >= 0 ? "positive" : "negative");

    const ivSkew = snap.iv_skew || 0;
    const skewEl = document.getElementById("m-iv-skew");
    skewEl.textContent = `${ivSkew.toFixed(2)} (${snap.skew_signal || "N/A"})`;
    skewEl.className = "value " +
        (snap.skew_signal === "BULLISH" || snap.skew_signal === "MILD BULL" ? "positive" :
         snap.skew_signal === "BEARISH" || snap.skew_signal === "MILD BEAR" ? "negative" : "neutral");

    document.getElementById("m-oi-dir").textContent = snap.net_oi_chg_direction || "N/A";
    document.getElementById("m-ce-oi").textContent = formatNum(snap.total_ce_oi_chg);
    document.getElementById("m-pe-oi").textContent = formatNum(snap.total_pe_oi_chg);
}

function updateStrikeChart(strikes) {
    // Use strike price as "time" axis (custom category via index)
    const ceData = [];
    const peData = [];

    strikes.forEach((s, i) => {
        // Use index as time for histogram series
        ceData.push({ time: s.strike, value: Math.abs(s.ce_gex || 0) });
        peData.push({ time: s.strike, value: -Math.abs(s.pe_gex || 0) });
    });

    // Histogram series needs time-sorted data, use strike as numeric time
    // LightweightCharts expects ascending time — strikes are already sorted
    ceGexSeries.setData(ceData);
    peGexSeries.setData(peData);
    gexChart.timeScale().fitContent();
}

function updateTimeline(snapshots) {
    const data = snapshots
        .filter(s => s.net_gex != null)
        .map(s => ({
            time: Math.floor(new Date(s.timestamp).getTime() / 1000),
            value: s.net_gex,
        }));

    if (data.length) {
        tlSeries.setData(data);
        tlChart.timeScale().fitContent();
    }
}

function addCandle(candle) {
    if (!candle) return;
    const bar = {
        time: typeof candle.time === "number" ? candle.time :
              Math.floor(new Date(candle.timestamp).getTime() / 1000),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
    };
    priceSeries.update(bar);
}

// ── Helpers ────────────────────────────────────────────────────────────────

function setLevel(id, val) {
    document.getElementById(id).textContent = val ? val.toFixed(0) : "--";
}

function setXauLevel(id, val) {
    document.getElementById(id).textContent = val ? "$" + val.toFixed(2) : "--";
}

function formatGex(val) {
    if (!val) return "--";
    const abs = Math.abs(val);
    if (abs >= 1e9) return (val / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (val / 1e6).toFixed(1) + "M";
    if (abs >= 1e3) return (val / 1e3).toFixed(0) + "K";
    return val.toFixed(0);
}

function formatNum(val) {
    if (val == null) return "--";
    if (val >= 1e6) return (val / 1e6).toFixed(1) + "M";
    if (val >= 1e3) return (val / 1e3).toFixed(0) + "K";
    if (val <= -1e6) return (val / 1e6).toFixed(1) + "M";
    if (val <= -1e3) return (val / 1e3).toFixed(0) + "K";
    return val.toLocaleString();
}

// ── Event Handlers ─────────────────────────────────────────────────────────

// Symbol tabs
document.querySelectorAll(".symbol-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".symbol-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        currentSymbol = tab.dataset.symbol;
        loadAll();
    });
});

// Date picker
const datePicker = document.getElementById("date-picker");
datePicker.value = currentDate;
datePicker.addEventListener("change", () => {
    currentDate = datePicker.value;
    loadAll();
});

// Timeframe tabs
document.querySelectorAll(".tf-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".tf-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        currentTF = parseInt(tab.dataset.tf);
        loadAll();
    });
});

// Refresh button
document.getElementById("btn-refresh").addEventListener("click", () => {
    const btn = document.getElementById("btn-refresh");
    btn.disabled = true;
    btn.textContent = "...";
    socket.emit("request_refresh", { symbol: currentSymbol });
});

// View tabs (Chart / Analytics)
document.querySelectorAll(".view-tab").forEach(tab => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".view-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");

        const view = tab.dataset.view;
        document.querySelectorAll(".tab-content").forEach(tc => tc.classList.remove("active"));
        document.getElementById("tab-" + view).classList.add("active");

        // Resize charts after tab switch (container dimensions change)
        setTimeout(() => {
            if (view === "chart") {
                const c = document.getElementById("price-chart");
                priceChart.applyOptions({ width: c.clientWidth, height: c.clientHeight });
                priceChart.timeScale().fitContent();
            } else {
                const gc = document.getElementById("gex-chart");
                const tc = document.getElementById("gex-timeline");
                gexChart.applyOptions({ width: gc.clientWidth });
                tlChart.applyOptions({ width: tc.clientWidth });
                gexChart.timeScale().fitContent();
                tlChart.timeScale().fitContent();
            }
        }, 50);
    });
});

// ── Boot ───────────────────────────────────────────────────────────────────

initPriceChart();
initGexChart();
initTimelineChart();
loadAll();
