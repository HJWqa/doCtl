// ============================================================
// Dobot 总控面板 - 断网/离线友好 · 零外部依赖
// 所有资源来自本机 Flask 静态目录，不访问公网 CDN。
// ============================================================

// 同源 Socket.IO：只连本机主控服务，不解析外网主机
const socket = io({
    path: "/socket.io",
    transports: ["websocket", "polling"],
    upgrade: true,
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionAttempts: Infinity,
    // 断网/服务重启时持续重连；不依赖任何外部地址
    rememberUpgrade: true,
});

const MAX_TRAFFIC = 240;
const trafficLines = [];
let activeTrafficDevice = "all";
let activeDebugTab = "traffic";
let lastStatus = null;       // 缓存最近一次状态，离线时不丢 UI
let lastTargets = null;      // 最近一次从剧本解析出的设备地址
let scriptParseTimer = null; // 编辑器输入防抖

const SCRIPT_DEFAULTS = {
    vision: { host: "127.0.0.1", port: "7930" },
    three_d: { host: "192.168.173.2", port: "9303" },
    bot: { host: "192.168.200.1", port: "9552" },
};

const trafficDeviceNames = {
    all: "全部",
    script: "Script",
    vision: "VS",
    camera3d: "3D",
    camera3d_legacy: "3D",
    arm: "Bot",
};

// ---------- 初始化 ----------
document.addEventListener("DOMContentLoaded", () => {
    bindUiEvents();
    loadScript();
    updateClock();
    setInterval(updateClock, 1000);
});

// 所有交互在 app.js 绑定，避免 CSP script-src 'self' 拦截 HTML 内联 onclick/onchange
function bindUiEvents() {
    document.querySelectorAll("[data-cmd]").forEach(btn => {
        btn.addEventListener("click", () => sendControl(btn.dataset.cmd));
    });

    const reloadBtn = document.getElementById("btn-script-reload");
    if (reloadBtn) reloadBtn.addEventListener("click", loadScript);

    const saveBtn = document.getElementById("btn-script-save");
    if (saveBtn) saveBtn.addEventListener("click", saveScript);

    const editor = document.getElementById("script-editor");
    if (editor) {
        // 输入时自动解析剧本，刷新左侧设备卡片地址
        editor.addEventListener("input", () => scheduleScriptParse());
        editor.addEventListener("change", () => scheduleScriptParse(true));
    }

    document.querySelectorAll(".tab-btn[data-tab]").forEach(btn => {
        btn.addEventListener("click", () => setDebugTab(btn.dataset.tab));
    });

    const clearBtn = document.getElementById("btn-traffic-clear");
    if (clearBtn) clearBtn.addEventListener("click", clearTraffic);

    const clearEventsBtn = document.getElementById("btn-events-clear");
    if (clearEventsBtn) clearEventsBtn.addEventListener("click", clearScriptEvents);

    const txFilter = document.getElementById("traffic-tx");
    if (txFilter) txFilter.addEventListener("change", filterTraffic);
    const rxFilter = document.getElementById("traffic-rx");
    if (rxFilter) rxFilter.addEventListener("change", filterTraffic);

    document.querySelectorAll(".traffic-tab[data-device]").forEach(btn => {
        btn.addEventListener("click", () => setTrafficDevice(btn.dataset.device));
    });
}

// ---------- Socket.IO 事件 ----------
socket.on("connect", () => {
    console.log("[WS] connected");
    setConnectionStatus(true);
    // 重连后如有缓存状态立即恢复
    if (lastStatus) updateStatus(lastStatus);
});

socket.on("disconnect", () => {
    console.log("[WS] disconnected");
    setConnectionStatus(false);
    updateAllDeviceStatus(false);
});

socket.on("connect_error", () => {
    setConnectionStatus(false);
});

socket.on("status", status => {
    lastStatus = status;          // 缓存状态
    updateStatus(status);
});

socket.on("log", data => appendLog(data.level, data.msg, data.ts));

socket.on("log_batch", data => {
    const container = document.getElementById("log-container");
    container.innerHTML = "";
    (data.logs || []).forEach(line => {
        const parsed = parseLogLine(line);
        appendLogRaw(parsed.level, parsed.msg, parsed.ts);
    });
});

socket.on("data_traffic", evt => {
    const line = {
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        device: evt.device_name || evt.device,
        direction: evt.direction,
        data: evt.data || "",
        deviceId: normalizeDevice(evt.device),
    };
    trafficLines.push(line);
    if (trafficLines.length > MAX_TRAFFIC) trafficLines.shift();
    updateTrafficCounts();
    if (activeDebugTab === "traffic" && isTrafficLineVisible(line)) {
        renderTrafficLine(line);
        const container = document.getElementById("traffic-container");
        container.scrollTop = container.scrollHeight;
    }
});

// ---------- 连接状态指示 ----------
function setConnectionStatus(connected) {
    const badge = document.getElementById("badge-mode");
    // 只在待机状态下覆盖显示连接状态，运行时仍显示运行状态
    const running = lastStatus && lastStatus.script && lastStatus.script.running;
    if (running) return;

    if (!connected) {
        badge.textContent = "离线";
        badge.className = "badge badge-offline";
    } else if (!lastStatus) {
        badge.textContent = "待机";
        badge.className = "badge";
    }
}

// ---------- 状态更新 ----------
function updateStatus(status) {
    const script = status.script || {};
    const running = !!script.running;
    const paused = !!script.paused;

    setDeviceStatus("script", running);
    setDeviceStatus("vision", !!(script.vision && script.vision.connected));
    setDeviceStatus("camera", !!(script.three_d && script.three_d.last_ok));
    setDeviceStatus("arm", !!(script.bot && script.bot.last_ok));

    document.getElementById("btn-start").disabled = running;
    document.getElementById("btn-stop").disabled = !running;
    document.getElementById("btn-pause").disabled = !running || paused;
    document.getElementById("btn-resume").disabled = !paused;

    updateBadge(running, paused);
    document.getElementById("stat-total").textContent = script.total_tasks || 0;
    document.getElementById("stat-ok").textContent = script.success_tasks || 0;
    document.getElementById("stat-fail").textContent = script.fail_tasks || 0;
    document.getElementById("watch-current").textContent =
        "当前步骤：" + (script.current_step || "待机");
    document.getElementById("watch-rx").textContent = script.last_rx || "--";
    document.getElementById("watch-tx").textContent = script.last_tx || "--";

    // 优先用编辑器当前剧本；编辑器为空时回退到后端 status 里的地址
    const editor = document.getElementById("script-editor");
    const editorText = editor ? editor.value : "";
    if (editorText.trim()) {
        updateTargetsFromText(editorText, {
            running: running,
            visionConnected: !!(script.vision && script.vision.connected),
        });
    } else {
        updateTargetsFromStatus(script);
    }
    renderEvents(script.events || []);
}

function setDeviceStatus(name, connected) {
    const status = document.getElementById("status-" + name);
    if (!status) return;
    status.textContent = connected ? "● 在线" : "○ 离线";
    status.classList.toggle("on", !!connected);
}

function updateAllDeviceStatus(connected) {
    ["script", "vision", "camera", "arm"].forEach(name => setDeviceStatus(name, connected));
}

function updateBadge(running, paused) {
    const badge = document.getElementById("badge-mode");
    if (!running) {
        badge.textContent = "待机";
        badge.className = "badge";
    } else if (paused) {
        badge.textContent = "暂停";
        badge.className = "badge badge-mock";
    } else {
        badge.textContent = "连接中";
        badge.className = "badge badge-real";
    }
}

function renderEvents(events) {
    const container = document.getElementById("event-list");
    container.innerHTML = "";
    if (!events.length) {
        container.innerHTML = '<div class="event-row is-empty">等待 VS 指令...</div>';
        return;
    }
    events.slice(-80).forEach(evt => {
        const row = document.createElement("div");
        row.className = "event-row event-" + (evt.level || "info");
        row.innerHTML = [
            '<span class="event-time">' + escapeHtml(evt.time || "") + '</span>',
            '<span class="event-step">' + escapeHtml(evt.step || "") + '</span>',
            '<span class="event-detail">' + escapeHtml(evt.detail || "") + '</span>',
        ].join("");
        container.appendChild(row);
    });
    container.scrollTop = container.scrollHeight;
}

/** 清空 Script 观察：同步清后端事件缓存，避免 status 推送把列表刷回来。 */
function clearScriptEvents() {
    renderEvents([]);
    document.getElementById("watch-rx").textContent = "--";
    document.getElementById("watch-tx").textContent = "--";
    sendControl("clear_events");
}

// ---------- 控制指令（双通道：Socket + HTTP）----------
function sendControl(cmd) {
    // 优先走 WebSocket（低延迟）
    if (socket.connected) {
        socket.emit("control", { cmd: cmd });
    }
    // HTTP 兜底（即使 WS 断开也能发指令）
    fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd: cmd }),
    }).catch(() => {});
}

// ---------- Script 读写 ----------
function loadScript() {
    fetch("/api/script")
        .then(r => r.json())
        .then(data => {
            document.getElementById("script-path").textContent = data.path || "--";
            document.getElementById("script-editor").value = data.text || "";
            updateTargetsFromText(data.text || "", connectionHintsFromStatus(lastStatus));
            setScriptMsg("已读取", "ok");
        })
        .catch(() => setScriptMsg("读取失败 (服务未连接)", "error"));
}

function saveScript() {
    const text = document.getElementById("script-editor").value;
    fetch("/api/script", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text }),
    })
        .then(async r => ({ ok: r.ok, data: await r.json() }))
        .then(({ ok, data }) => {
            if (!ok || data.status === "error") {
                setScriptMsg(data.message || "保存失败", "error");
                return;
            }
            updateTargetsFromText(text, connectionHintsFromStatus(lastStatus));
            setScriptMsg("已保存", "ok");
        })
        .catch(() => setScriptMsg("保存失败 (服务未连接)", "error"));
}

function setScriptMsg(text, cls) {
    const el = document.getElementById("script-msg");
    el.textContent = text;
    el.className = cls || "";
}

function scheduleScriptParse(immediate) {
    if (scriptParseTimer) {
        clearTimeout(scriptParseTimer);
        scriptParseTimer = null;
    }
    const run = () => {
        scriptParseTimer = null;
        const editor = document.getElementById("script-editor");
        updateTargetsFromText(editor ? editor.value : "", connectionHintsFromStatus(lastStatus));
    };
    if (immediate) {
        run();
        return;
    }
    scriptParseTimer = setTimeout(run, 180);
}

function connectionHintsFromStatus(status) {
    const script = (status && status.script) || {};
    return {
        running: !!script.running,
        visionConnected: !!(script.vision && script.vision.connected),
    };
}

/**
 * 从 TOML 剧本文本解析 [vision] / [three_d] / [bot] 并刷新设备卡片。
 * hints: { running, visionConnected } 用于 Script 主控副标题连接态文案。
 */
function updateTargetsFromText(text, hints) {
    const targets = parseScriptTargets(text);
    lastTargets = targets;
    applyDeviceTargets(targets, hints || connectionHintsFromStatus(lastStatus));
}

/** 编辑器为空时，用后端 status 中的地址填充卡片。 */
function updateTargetsFromStatus(script) {
    const vision = script.vision || {};
    const threeD = script.three_d || {};
    const bot = script.bot || {};
    const targets = {
        vision: {
            host: String(vision.host || SCRIPT_DEFAULTS.vision.host),
            port: String(vision.port || SCRIPT_DEFAULTS.vision.port),
        },
        three_d: {
            host: String(threeD.host || SCRIPT_DEFAULTS.three_d.host),
            port: String(threeD.port || SCRIPT_DEFAULTS.three_d.port),
        },
        bot: {
            host: String(bot.host || SCRIPT_DEFAULTS.bot.host),
            port: String(bot.port || SCRIPT_DEFAULTS.bot.port),
            dryRun: false,
        },
    };
    lastTargets = targets;
    applyDeviceTargets(targets, {
        running: !!script.running,
        visionConnected: !!(vision.connected),
    });
}

function applyDeviceTargets(targets, hints) {
    const vision = targets.vision;
    const threeD = targets.three_d;
    const bot = targets.bot;
    const visionAddr = vision.host + ":" + vision.port;
    const threeAddr = threeD.host + ":" + threeD.port;
    let botAddr = bot.host + ":" + bot.port;
    if (bot.dryRun) botAddr += " (dry-run)";

    // Script 主控只显示自身运行态，VS 地址只在 Vision 卡上展示，避免重复
    const running = !!(hints && hints.running);
    const paused = !!(lastStatus && lastStatus.script && lastStatus.script.paused);
    let scriptLabel = "待机 · 未启动";
    if (running && paused) scriptLabel = "已启动 · 暂停中";
    else if (running) scriptLabel = "已启动 · 运行中";

    setText("script-listen", scriptLabel);
    setText("vision-target", visionAddr);
    setText("three-d-target", threeAddr);
    setText("bot-target", botAddr);
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

/**
 * 轻量 TOML 段解析：只取顶层 [vision] / [three_d] / [bot] 的 host/port/dry_run。
 * 不依赖外部库，适合断网环境。
 */
function parseScriptTargets(text) {
    const raw = String(text || "");
    const visionHost = matchTomlValue(raw, "vision", "host") || SCRIPT_DEFAULTS.vision.host;
    const visionPort = matchTomlValue(raw, "vision", "port") || SCRIPT_DEFAULTS.vision.port;
    const threeHost = matchTomlValue(raw, "three_d", "host") || SCRIPT_DEFAULTS.three_d.host;
    const threePort = matchTomlValue(raw, "three_d", "port") || SCRIPT_DEFAULTS.three_d.port;
    const botHost = matchTomlValue(raw, "bot", "host") || SCRIPT_DEFAULTS.bot.host;
    const botPort = matchTomlValue(raw, "bot", "port") || SCRIPT_DEFAULTS.bot.port;
    const dryRaw = (matchTomlValue(raw, "bot", "dry_run") || "").toLowerCase();
    const dryRun = dryRaw === "true" || dryRaw === "1";

    return {
        vision: { host: visionHost, port: visionPort },
        three_d: { host: threeHost, port: threePort },
        bot: { host: botHost, port: botPort, dryRun: dryRun },
    };
}

function matchTomlValue(text, section, key) {
    // 匹配 [section] 到下一个顶层 [ 或文件结束；忽略 [[array]] 表
    const secRe = new RegExp(
        "(?:^|\\n)\\[" + escapeRegExp(section) + "\\]\\s*(?:\\n|$)([\\s\\S]*?)(?=(?:\\n\\[[^\\[]|\\n\\[\\[|$))"
    );
    const block = text.match(secRe);
    if (!block) return "";

    // key = "value" | 'value' | bare
    const lineRe = new RegExp(
        "(?:^|\\n)\\s*" + escapeRegExp(key) + "\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\n#]+))",
        "m"
    );
    const line = block[1].match(lineRe);
    if (!line) return "";
    const value = (line[1] != null ? line[1] : (line[2] != null ? line[2] : line[3] || ""));
    return String(value).trim().replace(/,\s*$/, "");
}

function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ---------- Debug 标签页 ----------
function setDebugTab(tab) {
    activeDebugTab = tab;
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    document.querySelectorAll(".tab-panel").forEach(panel => {
        panel.classList.toggle("active", panel.id === "tab-" + tab);
    });
    if (tab === "traffic") renderTraffic();
}

function renderTrafficLine(line) {
    const container = document.getElementById("traffic-container");
    const ph = container.querySelector(".traffic-placeholder");
    if (ph) ph.remove();
    if (!isTrafficLineVisible(line)) return;

    const el = document.createElement("div");
    el.className = "traffic-line";
    el.dataset.dir = line.direction;
    el.dataset.device = line.deviceId;
    const dirMap = { tx: "TX", rx: "RX", info: "INFO", err: "ERR" };
    el.innerHTML = [
        '<span class="traffic-time">' + line.time + '</span>',
        '<span class="traffic-device">' + escapeHtml(trafficDeviceNames[line.deviceId] || line.device) + '</span>',
        '<span class="traffic-dir ' + line.direction + '">' + (dirMap[line.direction] || line.direction) + '</span>',
        '<span class="traffic-data' + (line.direction === "err" ? " is-error" : "") + '">' + escapeHtml(line.data) + '</span>',
    ].join("");
    container.appendChild(el);
    while (container.children.length > MAX_TRAFFIC) container.firstChild.remove();
}

function renderTraffic() {
    const container = document.getElementById("traffic-container");
    const lines = trafficLines.filter(isTrafficLineVisible);
    container.innerHTML = "";
    if (!lines.length) {
        container.innerHTML = '<div class="traffic-line traffic-placeholder">' +
            (trafficDeviceNames[activeTrafficDevice] || "当前设备") + '暂无通信数据</div>';
        updateTrafficCounts();
        return;
    }
    lines.forEach(renderTrafficLine);
    container.scrollTop = container.scrollHeight;
    updateTrafficCounts();
}

function filterTraffic() {
    renderTraffic();
}

function clearTraffic() {
    if (activeTrafficDevice === "all") {
        trafficLines.length = 0;
    } else {
        for (let i = trafficLines.length - 1; i >= 0; i--) {
            if (trafficLines[i].deviceId === activeTrafficDevice) trafficLines.splice(i, 1);
        }
    }
    renderTraffic();
}

function setTrafficDevice(device) {
    activeTrafficDevice = trafficDeviceNames[device] ? device : "all";
    document.querySelectorAll(".traffic-tab").forEach(tab => {
        const active = tab.dataset.device === activeTrafficDevice;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    renderTraffic();
}

function isTrafficLineVisible(line) {
    if (activeTrafficDevice !== "all" && line.deviceId !== activeTrafficDevice) return false;
    if (line.direction === "tx" && !document.getElementById("traffic-tx").checked) return false;
    if (line.direction === "rx" && !document.getElementById("traffic-rx").checked) return false;
    return true;
}

function updateTrafficCounts() {
    const counts = { all: trafficLines.length, script: 0, vision: 0, camera3d: 0, arm: 0 };
    trafficLines.forEach(line => {
        const id = normalizeDevice(line.deviceId);
        if (Object.prototype.hasOwnProperty.call(counts, id)) counts[id]++;
    });
    Object.entries(counts).forEach(([device, count]) => {
        const el = document.getElementById("traffic-tab-count-" + device);
        if (el) el.textContent = count;
    });
    document.getElementById("traffic-count").textContent = counts[activeTrafficDevice] ?? counts.all;
}

function normalizeDevice(device) {
    if (device === "rk_auto" || device === "task3" || device === "camera3d_legacy") return "camera3d";
    return device || "script";
}

// ---------- 日志 ----------
function appendLog(level, msg, ts) {
    appendLogRaw(level, msg, ts);
    const container = document.getElementById("log-container");
    container.scrollTop = container.scrollHeight;
}

function appendLogRaw(level, msg, ts) {
    const el = document.createElement("div");
    el.className = "log-line log-" + String(level || "info").toLowerCase();
    el.textContent = "[" + (ts || "") + "] " + msg;
    document.getElementById("log-container").appendChild(el);
}

function parseLogLine(line) {
    const m = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s+\[(\w+)\]\s+(.*)/);
    if (m) return { ts: m[1], level: m[2], msg: m[3] };
    return { ts: "", level: "INFO", msg: line };
}

// ---------- 时钟 ----------
function updateClock() {
    document.getElementById("clock").textContent =
        new Date().toLocaleString("zh-CN", { hour12: false });
}

// ---------- 工具函数 ----------
function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
}
