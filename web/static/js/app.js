const socket = io();

const MAX_TRAFFIC = 240;
const trafficLines = [];
let activeTrafficDevice = "all";
let activeDebugTab = "traffic";

const trafficDeviceNames = {
    all: "全部",
    script: "Script",
    vision: "VS",
    camera3d: "3D",
    camera3d_legacy: "3D",
    arm: "Bot",
};

document.addEventListener("DOMContentLoaded", () => {
    loadScript();
    updateClock();
    setInterval(updateClock, 1000);
});

socket.on("connect", () => console.log("[WS] connected"));
socket.on("disconnect", () => updateAllDeviceStatus(false));
socket.on("status", updateStatus);
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

function updateStatus(status) {
    const script = status.script || {};
    const running = !!script.running;
    const paused = !!script.paused;

    setDeviceStatus("script", running);
    setDeviceStatus("vision", running);
    setDeviceStatus("camera", running);
    setDeviceStatus("arm", running);

    document.getElementById("btn-start").disabled = running;
    document.getElementById("btn-stop").disabled = !running;
    document.getElementById("btn-pause").disabled = !running || paused;
    document.getElementById("btn-resume").disabled = !paused;

    updateBadge(running, paused);
    document.getElementById("stat-total").textContent = script.total_tasks || 0;
    document.getElementById("stat-ok").textContent = script.success_tasks || 0;
    document.getElementById("stat-fail").textContent = script.fail_tasks || 0;
    document.getElementById("watch-current").textContent = `当前步骤：${script.current_step || "待机"}`;
    document.getElementById("watch-rx").textContent = script.last_rx || "--";
    document.getElementById("watch-tx").textContent = script.last_tx || "--";

    const listen = script.listen || {};
    document.getElementById("script-listen").textContent = `${listen.host || "0.0.0.0"}:${listen.port || 7950}`;
    renderEvents(script.events || []);
}

function setDeviceStatus(name, connected) {
    const status = document.getElementById(`status-${name}`);
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
        badge.textContent = "监听中";
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
        row.className = `event-row event-${evt.level || "info"}`;
        row.innerHTML = [
            `<span class="event-time">${escapeHtml(evt.time || "")}</span>`,
            `<span class="event-step">${escapeHtml(evt.step || "")}</span>`,
            `<span class="event-detail">${escapeHtml(evt.detail || "")}</span>`,
        ].join("");
        container.appendChild(row);
    });
    container.scrollTop = container.scrollHeight;
}

function sendControl(cmd) {
    socket.emit("control", { cmd });
    fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd }),
    }).catch(() => {});
}

function loadScript() {
    fetch("/api/script")
        .then(r => r.json())
        .then(data => {
            document.getElementById("script-path").textContent = data.path || "--";
            document.getElementById("script-editor").value = data.text || "";
            updateTargetsFromText(data.text || "");
            setScriptMsg("已读取", "ok");
        })
        .catch(() => setScriptMsg("读取失败", "error"));
}

function saveScript() {
    const text = document.getElementById("script-editor").value;
    fetch("/api/script", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
    })
        .then(async r => ({ ok: r.ok, data: await r.json() }))
        .then(({ ok, data }) => {
            if (!ok || data.status === "error") {
                setScriptMsg(data.message || "保存失败", "error");
                return;
            }
            updateTargetsFromText(text);
            setScriptMsg("已保存", "ok");
        })
        .catch(() => setScriptMsg("保存失败", "error"));
}

function setScriptMsg(text, cls) {
    const el = document.getElementById("script-msg");
    el.textContent = text;
    el.className = cls || "";
}

function updateTargetsFromText(text) {
    const threeHost = matchTomlValue(text, "three_d", "host") || "192.168.173.2";
    const threePort = matchTomlValue(text, "three_d", "port") || "9303";
    const botHost = matchTomlValue(text, "bot", "host") || "192.168.200.1";
    const botPort = matchTomlValue(text, "bot", "port") || "9552";
    document.getElementById("three-d-target").textContent = `${threeHost}:${threePort}`;
    document.getElementById("bot-target").textContent = `${botHost}:${botPort}`;
}

function matchTomlValue(text, section, key) {
    const re = new RegExp(`\\[${section}\\]([\\s\\S]*?)(?:\\n\\[|$)`);
    const block = text.match(re);
    if (!block) return "";
    const line = block[1].match(new RegExp(`^\\s*${key}\\s*=\\s*"?([^"\\n#]+)"?`, "m"));
    return line ? line[1].trim() : "";
}

function setDebugTab(tab) {
    activeDebugTab = tab;
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    document.querySelectorAll(".tab-panel").forEach(panel => {
        panel.classList.toggle("active", panel.id === `tab-${tab}`);
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
        `<span class="traffic-time">${line.time}</span>`,
        `<span class="traffic-device">${escapeHtml(trafficDeviceNames[line.deviceId] || line.device)}</span>`,
        `<span class="traffic-dir ${line.direction}">${dirMap[line.direction] || line.direction}</span>`,
        `<span class="traffic-data ${line.direction === "err" ? "is-error" : ""}">${escapeHtml(line.data)}</span>`,
    ].join("");
    container.appendChild(el);
    while (container.children.length > MAX_TRAFFIC) container.firstChild.remove();
}

function renderTraffic() {
    const container = document.getElementById("traffic-container");
    const lines = trafficLines.filter(isTrafficLineVisible);
    container.innerHTML = "";
    if (!lines.length) {
        container.innerHTML = `<div class="traffic-line traffic-placeholder">${trafficDeviceNames[activeTrafficDevice] || "当前设备"}暂无通信数据</div>`;
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
        const el = document.getElementById(`traffic-tab-count-${device}`);
        if (el) el.textContent = count;
    });
    document.getElementById("traffic-count").textContent = counts[activeTrafficDevice] ?? counts.all;
}

function normalizeDevice(device) {
    if (device === "rk_auto" || device === "task3" || device === "camera3d_legacy") return "camera3d";
    return device || "script";
}

function appendLog(level, msg, ts) {
    appendLogRaw(level, msg, ts);
    const container = document.getElementById("log-container");
    container.scrollTop = container.scrollHeight;
}

function appendLogRaw(level, msg, ts) {
    const el = document.createElement("div");
    el.className = `log-line log-${String(level || "info").toLowerCase()}`;
    el.textContent = `[${ts || ""}] ${msg}`;
    document.getElementById("log-container").appendChild(el);
}

function parseLogLine(line) {
    const m = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s+\[(\w+)\]\s+(.*)/);
    if (m) return { ts: m[1], level: m[2], msg: m[3] };
    return { ts: "", level: "INFO", msg: line };
}

function updateClock() {
    document.getElementById("clock").textContent =
        new Date().toLocaleString("zh-CN", { hour12: false });
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
}
