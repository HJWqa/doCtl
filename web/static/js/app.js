/**
 * Dobot 总控面板 — 前端逻辑
 */

// ============================================================
// Socket.IO 连接
// ============================================================
const socket = io();

// 页面加载时拉取配置
document.addEventListener("DOMContentLoaded", () => {
    loadConfig();
});

socket.on("connect", () => {
    console.log("[WS] 已连接");
});

socket.on("disconnect", () => {
    console.log("[WS] 已断开");
    updateAllDeviceStatus(false);
});

// ---------- 状态推送 ----------
socket.on("status", (status) => {
    updateStatus(status);
});

// ---------- 实时日志 ----------
socket.on("log", (data) => {
    appendLog(data.level, data.msg, data.ts);
});

// ---------- 批量日志 (连接时回放) ----------
socket.on("log_batch", (data) => {
    const container = document.getElementById("log-container");
    container.innerHTML = "";
    (data.logs || []).forEach(line => {
        const parsed = parseLogLine(line);
        appendLogRaw(parsed.level, parsed.msg, parsed.ts);
    });
});

// ============================================================
// 状态更新
// ============================================================
function updateStatus(s) {
    // 设备连接状态
    setDeviceStatus("vision", s.devices && s.devices.vision);
    setDeviceStatus("camera", s.devices && (s.devices.three_d_http || s.devices.rk_auto || s.devices.task3 || s.devices.camera3d_legacy));
    setDeviceStatus("arm", s.devices && s.devices.arm);
    setDeviceStatus("script", s.devices && s.devices.script);

    // 按钮状态
    const script = s.script || {};
    const running = !!script.running;
    const paused = !!script.paused;
    document.getElementById("btn-start").disabled = running;
    document.getElementById("btn-pause").disabled = !running || paused;
    document.getElementById("btn-resume").disabled = !paused;
    document.getElementById("btn-stop").disabled = !running;

    // 运行模式
    updateBadge(s.running, s.paused);

    // 统计
    document.getElementById("stat-total").textContent = script.total_tasks || s.total_tasks || 0;
    document.getElementById("stat-ok").textContent = script.success_tasks || s.success_tasks || 0;
    document.getElementById("stat-fail").textContent = script.fail_tasks || s.fail_tasks || 0;

    // 机械臂繁忙
    const armBusy = s.arm_busy;
    document.getElementById("wf-arm").classList.toggle("active", armBusy);
    if (armBusy) {
        document.getElementById("coord-result").textContent = "执行中...";
    }
}

function setDeviceStatus(name, connected) {
    const card = document.getElementById(`card-${name}`);
    const status = document.getElementById(`status-${name}`);
    if (!card || !status) return;
    if (connected) {
        status.textContent = "● 在线";
        status.classList.add("on");
    } else {
        status.textContent = "○ 离线";
        status.classList.remove("on");
    }
}

function updateAllDeviceStatus(connected) {
    ["vision", "camera", "arm", "script"].forEach(name => setDeviceStatus(name, connected));
}

function updateBadge(running, paused) {
    const badge = document.getElementById("badge-mode");
    if (!running) {
        badge.textContent = "待机";
        badge.className = "badge";
    } else if (paused) {
        badge.textContent = "已暂停";
        badge.className = "badge badge-mock";
    } else {
        badge.textContent = "运行中";
        badge.className = "badge badge-real";
    }
}

// ============================================================
// 日志
// ============================================================
function appendLog(level, msg, ts) {
    const cls = `log-${level.toLowerCase()}`;
    const el = document.createElement("div");
    el.className = `log-line ${cls}`;
    el.textContent = `[${ts}] ${msg}`;
    const container = document.getElementById("log-container");
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
}

function appendLogRaw(level, msg, ts) {
    const cls = `log-${level.toLowerCase()}`;
    const el = document.createElement("div");
    el.className = `log-line ${cls}`;
    el.textContent = `[${ts}] ${msg}`;
    document.getElementById("log-container").appendChild(el);
}

function parseLogLine(line) {
    // 格式: [HH:MM:SS] [LEVEL] message
    const m = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s+\[(\w+)\]\s+(.*)/);
    if (m) {
        return { ts: m[1], level: m[2], msg: m[3] };
    }
    return { ts: "", level: "INFO", msg: line };
}

// ============================================================
// 控制指令
// ============================================================
function sendControl(cmd) {
    socket.emit("control", { cmd });
    // 同时发 HTTP 请求作为双保险
    fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd }),
    }).catch(() => {});
}

// ============================================================
// 手动移动
// ============================================================
function manualMove() {
    const pose = {
        x: parseFloat(document.getElementById("man-x").value) || 200,
        y: parseFloat(document.getElementById("man-y").value) || 100,
        z: parseFloat(document.getElementById("man-z").value) || 50,
        rx: parseFloat(document.getElementById("man-rx").value) || 180,
        ry: parseFloat(document.getElementById("man-ry").value) || 0,
        rz: parseFloat(document.getElementById("man-rz").value) || 0,
    };
    fetch("/api/arm/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pose }),
    })
    .then(r => r.json())
    .then(data => {
        if (data && data.status === "ok") {
            document.getElementById("coord-pose").textContent =
                `X=${pose.x} Y=${pose.y} Z=${pose.z} R=${pose.rx},${pose.ry},${pose.rz}`;
        }
    })
    .catch(() => {});
}

// ============================================================
// 时钟
// ============================================================
function updateClock() {
    const now = new Date();
    document.getElementById("clock").textContent =
        now.toLocaleString("zh-CN", { hour12: false });
}
updateClock();
setInterval(updateClock, 1000);

// ============================================================
// 设置面板
// ============================================================

function toggleSettings() {
    const body = document.getElementById("set-body");
    const arrow = document.getElementById("set-arrow");
    const collapsed = body.classList.toggle("collapsed");
    arrow.textContent = collapsed ? "▶" : "▼";
}

function loadConfig() {
    fetch("/api/config")
        .then(r => r.json())
        .then(cfg => {
            document.getElementById("cfg-vision-host").value = cfg.VISION_HOST || "127.0.0.1";
            document.getElementById("cfg-vision-port").value = cfg.VISION_PORT || 9550;
            document.getElementById("cfg-camera-host").value = cfg.CAMERA3D_HOST || "192.168.173.2";
            document.getElementById("cfg-camera-port").value = cfg.CAMERA3D_PORT || 9551;
            document.getElementById("cfg-rk-auto-host").value = cfg.RK_AUTO_HOST || "192.168.173.2";
            document.getElementById("cfg-rk-auto-port").value = cfg.RK_AUTO_PORT || 9200;
            document.getElementById("cfg-task3-host").value = cfg.TASK3_HOST || "192.168.173.2";
            document.getElementById("cfg-task3-port").value = cfg.TASK3_PORT || 9103;
            document.getElementById("cfg-arm-host").value = cfg.ARM_HOST || "192.168.200.1";
            document.getElementById("cfg-arm-port").value = cfg.ARM_PORT || 9552;
            document.getElementById("cfg-mock").checked = cfg.MOCK_MODE !== false;
            // 更新模式 badge
            updateBadgeFromMock(cfg.MOCK_MODE !== false);
        })
        .catch(() => {});
}

function saveConfig() {
    const updates = {
        VISION_HOST: document.getElementById("cfg-vision-host").value.trim(),
        VISION_PORT: parseInt(document.getElementById("cfg-vision-port").value) || 9550,
        CAMERA3D_HOST: document.getElementById("cfg-camera-host").value.trim(),
        CAMERA3D_PORT: parseInt(document.getElementById("cfg-camera-port").value) || 9551,
        RK_AUTO_HOST: document.getElementById("cfg-rk-auto-host").value.trim(),
        RK_AUTO_PORT: parseInt(document.getElementById("cfg-rk-auto-port").value) || 9200,
        TASK3_HOST: document.getElementById("cfg-task3-host").value.trim(),
        TASK3_PORT: parseInt(document.getElementById("cfg-task3-port").value) || 9103,
        ARM_HOST: document.getElementById("cfg-arm-host").value.trim(),
        ARM_PORT: parseInt(document.getElementById("cfg-arm-port").value) || 9552,
        MOCK_MODE: document.getElementById("cfg-mock").checked,
    };

    const msg = document.getElementById("cfg-msg");
    msg.textContent = "保存中...";
    msg.className = "";

    fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
    })
    .then(r => r.json())
    .then(result => {
        if (result.ok && result.ok.length > 0) {
            msg.textContent = "✓ 已保存并重连";
            msg.className = "ok";
            updateBadgeFromMock(updates.MOCK_MODE);
            // 回读确认
            setTimeout(loadConfig, 500);
        } else if (result.error) {
            msg.textContent = "✗ 保存失败: " + result.error;
            msg.className = "error";
        } else {
            msg.textContent = "✗ 无有效变更";
            msg.className = "error";
        }
    })
    .catch(e => {
        msg.textContent = "✗ 请求失败";
        msg.className = "error";
    });
}

// ============================================================
// 通信数据监控
// ============================================================

const MAX_TRAFFIC = 200;
const trafficLines = [];
let activeTrafficDevice = "all";
const trafficDeviceNames = {
    all: "全部",
    vision: "2D视觉",
    camera3d: "3D相机",
    camera3d_legacy: "3D旧链路",
    rk_auto: "RK自动3D",
    task3: "任务三桥接",
    script: "Script主控",
    arm: "机械臂",
};

socket.on("data_traffic", (evt) => {
    // 构建行数据
    const line = {
        time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
        device: evt.device_name || evt.device,
        direction: evt.direction,  // "tx" or "rx"
        data: evt.data || "",
        deviceId: evt.device,
    };

    trafficLines.push(line);
    if (trafficLines.length > MAX_TRAFFIC) trafficLines.shift();

    updateTrafficCounts();
    if (isTrafficLineVisible(line)) {
        renderTrafficLine(line);

        // 滚动到底部
        const container = document.getElementById("traffic-container");
        container.scrollTop = container.scrollHeight;
    } else if (!getVisibleTrafficLines().length) {
        renderTraffic();
    }
});

function renderTrafficLine(line) {
    const container = document.getElementById("traffic-container");
    // 清除占位符
    const ph = container.querySelector(".traffic-placeholder");
    if (ph) ph.remove();

    const el = document.createElement("div");
    el.className = "traffic-line";
    el.dataset.dir = line.direction;
    el.dataset.device = line.deviceId;

    const txChecked = document.getElementById("traffic-tx").checked;
    const rxChecked = document.getElementById("traffic-rx").checked;
    if (line.direction === "tx" && !txChecked) return;
    if (line.direction === "rx" && !rxChecked) return;
    if (!isTrafficDeviceVisible(line)) return;

    const dirMap = { tx: "→", rx: "←", info: "ℹ", err: "✗" };
    const dirClass = line.direction;
    const dirSymbol = dirMap[line.direction] || line.direction;

    el.innerHTML = [
        `<span class="traffic-time">${line.time}</span>`,
        `<span class="traffic-device">${line.device}</span>`,
        `<span class="traffic-dir ${dirClass}">${dirSymbol}</span>`,
        `<span class="traffic-data ${line.direction === 'err' ? 'is-error' : ''}">${escapeHtml(line.data)}</span>`,
    ].join("");

    container.appendChild(el);

    // 限制 DOM 行数
    while (container.children.length > MAX_TRAFFIC) {
        container.firstChild.remove();
    }
}

function filterTraffic() {
    renderTraffic();
}

function clearTraffic() {
    if (activeTrafficDevice === "all") {
        trafficLines.length = 0;
    } else {
        for (let i = trafficLines.length - 1; i >= 0; i--) {
            if (trafficLines[i].deviceId === activeTrafficDevice) {
                trafficLines.splice(i, 1);
            }
        }
    }
    updateTrafficCounts();
    const container = document.getElementById("traffic-container");
    container.innerHTML = `<div class="traffic-line traffic-placeholder">${trafficDeviceNames[activeTrafficDevice] || "当前设备"}已清空</div>`;
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
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

function renderTraffic() {
    const container = document.getElementById("traffic-container");
    const visibleLines = getVisibleTrafficLines();

    container.innerHTML = "";
    if (!visibleLines.length) {
        const empty = document.createElement("div");
        empty.className = "traffic-line traffic-placeholder";
        empty.textContent = `${trafficDeviceNames[activeTrafficDevice] || "当前设备"}暂无通信数据`;
        container.appendChild(empty);
        updateTrafficCounts();
        return;
    }

    visibleLines.forEach(renderTrafficLine);
    container.scrollTop = container.scrollHeight;
    updateTrafficCounts();
}

function getVisibleTrafficLines() {
    return trafficLines.filter(isTrafficLineVisible);
}

function isTrafficLineVisible(line) {
    if (!isTrafficDeviceVisible(line)) return false;
    const txChecked = document.getElementById("traffic-tx").checked;
    const rxChecked = document.getElementById("traffic-rx").checked;
    if (line.direction === "tx") return txChecked;
    if (line.direction === "rx") return rxChecked;
    return true;
}

function isTrafficDeviceVisible(line) {
    return activeTrafficDevice === "all" || line.deviceId === activeTrafficDevice;
}

function updateTrafficCounts() {
    const counts = {
        all: trafficLines.length,
        vision: 0,
        camera3d: 0,
        camera3d_legacy: 0,
        rk_auto: 0,
        task3: 0,
        script: 0,
        arm: 0,
    };

    trafficLines.forEach(line => {
        if (Object.prototype.hasOwnProperty.call(counts, line.deviceId)) {
            counts[line.deviceId]++;
        }
    });

    Object.entries(counts).forEach(([device, count]) => {
        const el = document.getElementById(`traffic-tab-count-${device}`);
        if (el) el.textContent = count;
    });

    document.getElementById("traffic-count").textContent = counts[activeTrafficDevice] ?? counts.all;
}

// ============================================================
// 连接测试
// ============================================================

function testConn(device) {
    const hostMap = {
        vision: "cfg-vision-host",
        camera: "cfg-camera-host",
        rk_auto: "cfg-rk-auto-host",
        task3: "cfg-task3-host",
        arm: "cfg-arm-host",
    };
    const portMap = {
        vision: "cfg-vision-port",
        camera: "cfg-camera-port",
        rk_auto: "cfg-rk-auto-port",
        task3: "cfg-task3-port",
        arm: "cfg-arm-port",
    };

    const host = document.getElementById(hostMap[device]).value.trim();
    const port = parseInt(document.getElementById(portMap[device]).value) || 0;
    const resultEl = document.getElementById(`test-${device}`);

    resultEl.textContent = "测试中...";
    resultEl.className = "test-result testing";

    fetch("/api/config/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host, port }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.reachable) {
            resultEl.textContent = "✓ 可达";
            resultEl.className = "test-result ok";
        } else {
            resultEl.textContent = "✗ " + (data.message || "不可达");
            resultEl.className = "test-result fail";
        }
    })
    .catch(() => {
        resultEl.textContent = "✗ 请求失败";
        resultEl.className = "test-result fail";
    });
}

function updateBadgeFromMock(mock) {
    const badge = document.getElementById("badge-mode");
    if (mock) {
        badge.textContent = "模拟模式";
        badge.className = "badge badge-mock";
    } else {
        badge.textContent = "真实模式";
        badge.className = "badge badge-real";
    }
}
