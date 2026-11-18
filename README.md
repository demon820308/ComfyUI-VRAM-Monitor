# ComfyUI-VRAM-Monitor

Top-bar real-time **VRAM / RAM / CPU** monitor with one-click GPU memory cleanup for ComfyUI.

## Features

- **Top-bar live telemetry**: Displays `VRAM used/total · RAM % · CPU %` right next to the Manager/Settings controls in ComfyUI's top menu bar, with automatic high-usage warnings (`>= 85%` amber, `>= 95%` red) and detailed hover tooltips.
- **One-click GPU memory cleanup**:
  - **Quick Clean (`清理`)**: Reuses `ComfyUI-Easy-Use` (`cleanGPUUsedForce`) when installed, or falls back to ComfyUI native `unload_all_models` + `soft_empty_cache` + `gc.collect()` + `torch.cuda.empty_cache()`.
  - **Deep Clean**: Also resets ComfyUI's execution cache (`free_memory`) before freeing GPU memory.
- **Safe & lightweight**: Checks the active/pending queue before cleaning to avoid interrupting running workflows, pauses polling when the browser tab is hidden, and adds zero extra canvas nodes.

## Installation

### Via ComfyUI Manager / Comfy Registry

Search for **ComfyUI VRAM Monitor** in ComfyUI-Manager, or install with `comfy-cli`:

```bash
comfy node install comfyui-vram-monitor
```

### Manual

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/demon820308/ComfyUI-VRAM-Monitor.git
```

Restart ComfyUI and refresh the browser.
