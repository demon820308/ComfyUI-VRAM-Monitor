"""Top-bar VRAM, RAM, and CPU monitor with one-click GPU memory cleanup."""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import sys
import threading
import time
from typing import Any, Callable

import psutil

logger = logging.getLogger("ComfyUI-VRAM-Monitor")

NODE_CLASS_MAPPINGS: dict[str, Any] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_STATS_TTL_SECONDS = 0.8
_cache_lock = threading.Lock()
_cached_stats: dict[str, Any] | None = None
_cached_at: float = 0.0

# Prime psutil's non-blocking CPU delta counter once at import time.
try:
    psutil.cpu_percent(interval=None)
except Exception:
    pass


def _is_queue_busy(prompt_queue: Any) -> bool:
    """Return True if any prompt is currently running or pending."""
    if prompt_queue is None:
        return False
    try:
        running, pending = prompt_queue.get_current_queue_volatile()
        return bool(running or pending)
    except Exception:
        return False


def _get_prompt_queue() -> Any:
    """Return the active ComfyUI PromptQueue instance, or None outside server runtime."""
    try:
        from server import PromptServer

        instance = getattr(PromptServer, "instance", None)
        return getattr(instance, "prompt_queue", None)
    except Exception:
        return None


def _get_vram_stats() -> dict[str, Any]:
    """Read real GPU VRAM usage, preferring CUDA mem_get_info for OS-level accuracy."""
    try:
        import torch

        if torch.cuda.is_available():
            dev_idx = torch.cuda.current_device()
            try:
                import comfy.model_management as mm

                torch_dev = mm.get_torch_device()
                if getattr(torch_dev, "type", None) == "cuda":
                    dev_idx = torch_dev if torch_dev.index is not None else dev_idx
            except Exception:
                pass

            free_bytes, total_bytes = torch.cuda.mem_get_info(dev_idx)
            used_bytes = max(0, total_bytes - free_bytes)
            allocated_bytes = torch.cuda.memory_allocated(dev_idx)
            return {
                "available": True,
                "device": torch.cuda.get_device_name(dev_idx),
                "used_bytes": int(used_bytes),
                "total_bytes": int(total_bytes),
                "free_bytes": int(free_bytes),
                "allocated_bytes": int(allocated_bytes),
                "percent": round(used_bytes / total_bytes * 100.0, 1) if total_bytes > 0 else 0.0,
            }
    except Exception as exc:
        logger.debug("CUDA VRAM probe failed: %s", exc)

    try:
        import comfy.model_management as mm

        dev = mm.get_torch_device()
        if getattr(dev, "type", "cpu") != "cpu":
            total_bytes = int(mm.get_total_memory(dev))
            free_bytes = int(mm.get_free_memory(dev))
            used_bytes = max(0, total_bytes - free_bytes)
            return {
                "available": total_bytes > 0,
                "device": mm.get_torch_device_name(dev),
                "used_bytes": used_bytes,
                "total_bytes": total_bytes,
                "free_bytes": free_bytes,
                "allocated_bytes": None,
                "percent": round(used_bytes / total_bytes * 100.0, 1) if total_bytes > 0 else 0.0,
            }
    except Exception as exc:
        logger.debug("Fallback VRAM probe failed: %s", exc)

    return {
        "available": False,
        "device": None,
        "used_bytes": 0,
        "total_bytes": 0,
        "free_bytes": 0,
        "allocated_bytes": None,
        "percent": 0.0,
    }


def _collect_stats(force: bool = False) -> dict[str, Any]:
    """Collect CPU, RAM, VRAM, and queue state with a short TTL cache."""
    global _cached_stats, _cached_at
    now = time.monotonic()
    with _cache_lock:
        if not force and _cached_stats is not None and (now - _cached_at) < _STATS_TTL_SECONDS:
            return _cached_stats

        vm = psutil.virtual_memory()
        cpu_percent = round(float(psutil.cpu_percent(interval=None)), 1)
        stats = {
            "cpu": {"percent": cpu_percent},
            "ram": {
                "used_bytes": int(vm.used),
                "total_bytes": int(vm.total),
                "available_bytes": int(vm.available),
                "percent": round(float(vm.percent), 1),
            },
            "vram": _get_vram_stats(),
            "queue_busy": _is_queue_busy(_get_prompt_queue()),
            "timestamp": time.time(),
        }
        _cached_stats = stats
        _cached_at = now
        return stats


def _find_easyuse_clean_fn() -> Callable[[], Any] | None:
    """Locate cleanGPUUsedForce from the already-loaded comfyui-easy-use custom node."""
    target_suffix = os.path.normcase(os.path.join("comfyui-easy-use", "py", "libs", "utils.py"))
    for mod in list(sys.modules.values()):
        mod_file = getattr(mod, "__file__", None)
        if not mod_file:
            continue
        if os.path.normcase(os.path.normpath(mod_file)).endswith(target_suffix):
            fn = getattr(mod, "cleanGPUUsedForce", None)
            if callable(fn):
                return fn
    return None


def _fallback_clean_gpu() -> None:
    """Best-effort ComfyUI memory cleanup when comfyui-easy-use is unavailable."""
    try:
        import comfy.model_management as mm

        mm.unload_all_models()
        mm.soft_empty_cache()
    except Exception as exc:
        logger.debug("model_management cleanup skipped: %s", exc)

    gc.collect()

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception as exc:
        logger.debug("CUDA empty_cache skipped: %s", exc)


async def _reset_execution_cache(prompt_queue: Any, timeout: float = 0.6) -> bool:
    """Request ComfyUI main loop to reset PromptExecutor caches and wait briefly for pickup."""
    if prompt_queue is None or not hasattr(prompt_queue, "set_flag"):
        return False
    prompt_queue.set_flag("free_memory", True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # PromptQueue.get_flags(reset=False) is non-destructive and thread-safe.
        get_flags = getattr(prompt_queue, "get_flags", None)
        if callable(get_flags):
            try:
                if not get_flags(reset=False).get("free_memory", False):
                    return True
            except TypeError:
                break
        await asyncio.sleep(0.02)
    return False


async def _run_cleanup(deep: bool = False) -> tuple[int, dict[str, Any]]:
    """Run VRAM cleanup (reusing easy cleanGpuUsed) and return (http_status, payload)."""
    prompt_queue = _get_prompt_queue()
    if _is_queue_busy(prompt_queue):
        return 409, {
            "ok": False,
            "reason": "queue_busy",
            "message": "有任务正在运行或排队中，请先停止后再清理显存",
        }

    before = _collect_stats(force=True)
    execution_cache_cleared = False
    if deep:
        execution_cache_cleared = await _reset_execution_cache(prompt_queue)

    easyuse_clean = _find_easyuse_clean_fn()
    if easyuse_clean is not None:
        await asyncio.to_thread(easyuse_clean)
        cleanup_source = "easyuse"
    else:
        await asyncio.to_thread(_fallback_clean_gpu)
        cleanup_source = "comfy_fallback"

    after = _collect_stats(force=True)
    freed_vram_bytes = max(0, before["vram"]["used_bytes"] - after["vram"]["used_bytes"])
    freed_ram_bytes = max(0, before["ram"]["used_bytes"] - after["ram"]["used_bytes"])
    return 200, {
        "ok": True,
        "deep": deep,
        "cleanup_source": cleanup_source,
        "execution_cache_cleared": execution_cache_cleared,
        "freed_vram_bytes": freed_vram_bytes,
        "freed_ram_bytes": freed_ram_bytes,
        "before": before,
        "after": after,
    }


def _register_routes() -> None:
    """Register HTTP routes on ComfyUI PromptServer when running inside ComfyUI."""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return

    instance = getattr(PromptServer, "instance", None)
    if instance is None or not hasattr(instance, "routes"):
        return

    @instance.routes.get("/vram_monitor/stats")
    async def get_vram_monitor_stats(request):
        force = request.rel_url.query.get("force") == "1"
        return web.json_response(_collect_stats(force=force))

    @instance.routes.post("/vram_monitor/clean")
    async def post_vram_monitor_clean(request):
        deep = False
        try:
            if request.can_read_body:
                body = await request.json()
                if isinstance(body, dict):
                    deep = bool(body.get("deep", False))
        except Exception:
            pass

        try:
            status, payload = await _run_cleanup(deep=deep)
            return web.json_response(payload, status=status)
        except Exception as exc:
            logger.exception("VRAM monitor cleanup failed")
            return web.json_response({"ok": False, "message": str(exc)}, status=500)


_register_routes()
