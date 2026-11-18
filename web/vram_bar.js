import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EXTENSION_NAME = "Comfy.VRAMMonitor.TopBar";
const ROOT_ID = "comfy-vram-monitor-bar";
const STYLE_ID = "comfy-vram-monitor-style";
const POLL_INTERVAL_MS = 2000;

function formatGB(bytes) {
    return (bytes / (1024 ** 3)).toFixed(1);
}

function formatMBorGB(bytes) {
    const gb = bytes / (1024 ** 3);
    if (gb >= 0.1) return `${gb.toFixed(2)} GB`;
    return `${(bytes / (1024 ** 2)).toFixed(0)} MB`;
}

function getLevelClass(percent) {
    if (percent >= 95) return "vram-mon-crit";
    if (percent >= 85) return "vram-mon-warn";
    return "";
}

function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
        #${ROOT_ID} {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            height: 28px;
            padding: 0 4px 0 8px;
            border-radius: 6px;
            background: var(--comfy-menu-secondary-bg, var(--comfy-menu-bg, rgba(128, 128, 128, 0.14)));
            border: 1px solid var(--border-color, rgba(128, 128, 128, 0.25));
            color: var(--input-text, var(--fg-color, inherit));
            font-size: 12px;
            line-height: 1;
            white-space: nowrap;
            user-select: none;
            box-sizing: border-box;
        }
        #${ROOT_ID} .vram-mon-metrics {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-variant-numeric: tabular-nums;
            padding-right: 2px;
        }
        #${ROOT_ID} .vram-mon-sep {
            opacity: 0.45;
        }
        #${ROOT_ID} .vram-mon-item {
            transition: color 0.15s ease;
        }
        #${ROOT_ID} .vram-mon-warn {
            color: #d97706;
            font-weight: 600;
        }
        #${ROOT_ID} .vram-mon-crit {
            color: var(--error-text, #ef4444);
            font-weight: 700;
        }
        #${ROOT_ID} .vram-mon-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 3px;
            height: 22px;
            padding: 0 7px;
            border-radius: 4px;
            border: 1px solid var(--border-color, rgba(128, 128, 128, 0.28));
            background: var(--comfy-input-bg, rgba(128, 128, 128, 0.16));
            color: var(--input-text, var(--fg-color, inherit));
            font-size: 11px;
            line-height: 1;
            cursor: pointer;
            transition: filter 0.15s ease, opacity 0.15s ease;
        }
        #${ROOT_ID} .vram-mon-btn:hover:not(:disabled) {
            filter: brightness(1.12);
        }
        #${ROOT_ID} .vram-mon-btn:disabled {
            opacity: 0.55;
            cursor: wait;
        }
        #${ROOT_ID} .vram-mon-btn-icon {
            padding: 0 5px;
        }
        #${ROOT_ID} .vram-mon-btn .mdi {
            font-size: 14px;
            line-height: 1;
        }
    `;
    document.head.appendChild(style);
}

function showToast(severity, summary, detail) {
    try {
        const toast = app?.extensionManager?.toast;
        if (toast?.add) {
            toast.add({ severity, summary, detail, life: 3200 });
            return;
        }
    } catch (_) {
        // Fall through to legacy toast.
    }
    const el = document.createElement("div");
    el.className = "comfy-toast";
    el.textContent = detail ? `${summary}: ${detail}` : summary;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3200);
}

function createBarElement() {
    const root = document.createElement("div");
    root.id = ROOT_ID;

    const metrics = document.createElement("div");
    metrics.className = "vram-mon-metrics";

    const vramEl = document.createElement("span");
    vramEl.className = "vram-mon-item";
    vramEl.textContent = "VRAM --";

    const sep1 = document.createElement("span");
    sep1.className = "vram-mon-sep";
    sep1.textContent = "·";

    const ramEl = document.createElement("span");
    ramEl.className = "vram-mon-item";
    ramEl.textContent = "RAM --%";

    const sep2 = document.createElement("span");
    sep2.className = "vram-mon-sep";
    sep2.textContent = "·";

    const cpuEl = document.createElement("span");
    cpuEl.className = "vram-mon-item";
    cpuEl.textContent = "CPU --%";

    metrics.append(vramEl, sep1, ramEl, sep2, cpuEl);

    const quickBtn = document.createElement("button");
    quickBtn.type = "button";
    quickBtn.className = "vram-mon-btn";
    quickBtn.title = "清理显存 (复用 easy cleanGpuUsed：卸载模型并清空显存缓存，保留节点执行缓存)";
    quickBtn.innerHTML = `<i class="mdi mdi-broom" aria-hidden="true"></i><span>清理</span>`;

    const deepBtn = document.createElement("button");
    deepBtn.type = "button";
    deepBtn.className = "vram-mon-btn vram-mon-btn-icon";
    deepBtn.title = "深度清理 (额外清空 ComfyUI 节点执行缓存，释放最彻底但下次运行需重算上游)";
    deepBtn.setAttribute("aria-label", "深度清理");
    deepBtn.innerHTML = `<i class="mdi mdi-delete-sweep-outline" aria-hidden="true"></i>`;

    root.append(metrics, quickBtn, deepBtn);
    return { root, metrics, vramEl, ramEl, cpuEl, quickBtn, deepBtn };
}

function renderStats(ui, data) {
    if (!data) return;
    const { vram, ram, cpu } = data;

    if (vram?.available) {
        ui.vramEl.textContent = `VRAM ${formatGB(vram.used_bytes)}/${formatGB(vram.total_bytes)}G`;
        ui.vramEl.className = `vram-mon-item ${getLevelClass(vram.percent)}`;
    } else {
        ui.vramEl.textContent = "VRAM N/A";
        ui.vramEl.className = "vram-mon-item";
    }

    if (ram) {
        ui.ramEl.textContent = `RAM ${Math.round(ram.percent)}%`;
        ui.ramEl.className = `vram-mon-item ${getLevelClass(ram.percent)}`;
    }

    if (cpu) {
        ui.cpuEl.textContent = `CPU ${Math.round(cpu.percent)}%`;
        ui.cpuEl.className = `vram-mon-item ${getLevelClass(cpu.percent)}`;
    }

    const lines = [];
    if (vram?.available) {
        const allocText =
            typeof vram.allocated_bytes === "number"
                ? `（PyTorch 模型占用 ${formatGB(vram.allocated_bytes)} GB）`
                : "";
        lines.push(
            `GPU: ${vram.device || "CUDA"} — ${formatGB(vram.used_bytes)} / ${formatGB(vram.total_bytes)} GB (${vram.percent}%)${allocText}`
        );
    } else {
        lines.push("GPU: 未检测到可用显存设备");
    }
    if (ram) {
        lines.push(
            `RAM: ${formatGB(ram.used_bytes)} / ${formatGB(ram.total_bytes)} GB (${ram.percent}%)`
        );
    }
    if (cpu) {
        lines.push(`CPU: ${cpu.percent}%`);
    }
    ui.metrics.title = lines.join("\n");
}

function mountTopBarElement(element) {
    if (app.menu?.settingsGroup?.element?.before) {
        const Group = window.comfyAPI?.buttonGroup?.ComfyButtonGroup;
        if (Group) {
            const group = new Group(element);
            app.menu.settingsGroup.element.before(group.element);
            return true;
        }
        app.menu.settingsGroup.element.before(element);
        return true;
    }

    const legacyMenu = document.querySelector(".comfy-menu");
    if (legacyMenu) {
        legacyMenu.append(element);
        return true;
    }
    return false;
}

app.registerExtension({
    name: EXTENSION_NAME,
    async setup() {
        if (document.getElementById(ROOT_ID)) return;
        injectStyles();

        const ui = createBarElement();
        if (!mountTopBarElement(ui.root)) {
            return;
        }

        let cleaning = false;
        let timer = null;

        async function refreshStats(force = false) {
            try {
                const url = force ? "/vram_monitor/stats?force=1" : "/vram_monitor/stats";
                const res = await api.fetchApi(url);
                if (!res.ok) return;
                renderStats(ui, await res.json());
            } catch (_) {
                // Ignore transient network errors during startup/restart.
            }
        }

        async function runClean(deep) {
            if (cleaning) return;
            cleaning = true;
            ui.quickBtn.disabled = true;
            ui.deepBtn.disabled = true;
            try {
                const { Running, Pending } = await api.getQueue();
                if ((Running && Running.length > 0) || (Pending && Pending.length > 0)) {
                    showToast("warn", "无法清理显存", "请先停止所有运行或排队中的任务");
                    return;
                }

                const res = await api.fetchApi("/vram_monitor/clean", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ deep }),
                });
                const payload = await res.json().catch(() => ({}));

                if (!res.ok || !payload.ok) {
                    showToast("warn", "清理未执行", payload.message || `HTTP ${res.status}`);
                    return;
                }

                if (payload.after) {
                    renderStats(ui, payload.after);
                } else {
                    await refreshStats(true);
                }

                const modeLabel = deep ? "深度清理完成" : "显存已清理";
                const freedVram = payload.freed_vram_bytes || 0;
                const freedRam = payload.freed_ram_bytes || 0;
                const detailParts = [];
                if (freedVram > 0) detailParts.push(`释放显存 ${formatMBorGB(freedVram)}`);
                if (freedRam > 0) detailParts.push(`释放内存 ${formatMBorGB(freedRam)}`);
                if (detailParts.length === 0) {
                    detailParts.push("已卸载缓存模型并清空 CUDA 缓存");
                }
                showToast("success", modeLabel, detailParts.join("，"));
            } catch (err) {
                showToast("error", "清理失败", err?.message || "请求异常");
            } finally {
                cleaning = false;
                ui.quickBtn.disabled = false;
                ui.deepBtn.disabled = false;
            }
        }

        ui.quickBtn.addEventListener("click", () => runClean(false));
        ui.deepBtn.addEventListener("click", () => runClean(true));

        function schedulePolling() {
            if (timer !== null) {
                clearInterval(timer);
                timer = null;
            }
            if (!document.hidden) {
                refreshStats(false);
                timer = setInterval(() => {
                    if (!document.hidden) refreshStats(false);
                }, POLL_INTERVAL_MS);
            }
        }

        document.addEventListener("visibilitychange", schedulePolling);
        schedulePolling();
    },
});
