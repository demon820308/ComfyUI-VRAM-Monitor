"""Unit tests for ComfyUI-VRAM-Monitor backend helpers."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT_PATH = os.path.join(PLUGIN_DIR, "__init__.py")

spec = importlib.util.spec_from_file_location("comfyui_vram_monitor_test_target", INIT_PATH)
assert spec is not None and spec.loader is not None
vram_monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vram_monitor)


class TestVRAMMonitor(unittest.TestCase):
    def setUp(self):
        vram_monitor._cached_stats = None
        vram_monitor._cached_at = 0.0

    def test_exports_empty_nodes_and_web_dir(self):
        self.assertEqual(vram_monitor.NODE_CLASS_MAPPINGS, {})
        self.assertEqual(vram_monitor.WEB_DIRECTORY, "./web")

    def test_collect_stats_uses_ttl_cache_unless_forced(self):
        with patch.object(vram_monitor, "_get_vram_stats", return_value={
            "available": True, "device": "MockGPU",
            "used_bytes": 4 * 1024**3, "total_bytes": 10 * 1024**3,
            "free_bytes": 6 * 1024**3, "allocated_bytes": 2 * 1024**3, "percent": 40.0,
        }) as mock_vram:
            s1 = vram_monitor._collect_stats(force=False)
            s2 = vram_monitor._collect_stats(force=False)
            self.assertEqual(mock_vram.call_count, 1)
            self.assertEqual(s1, s2)
            vram_monitor._collect_stats(force=True)
            self.assertEqual(mock_vram.call_count, 2)
            self.assertIn("cpu", s1)
            self.assertIn("ram", s1)
            self.assertTrue(s1["vram"]["available"])

    def test_find_easyuse_clean_fn_matches_suffix_only(self):
        fake_mod = types.ModuleType("fake_easyuse_utils")
        fake_mod.__file__ = r"K:\ComfyUi\ComfyUI\ComfyUI\custom_nodes\comfyui-easy-use\py\libs\utils.py"
        marker_fn = MagicMock()
        fake_mod.cleanGPUUsedForce = marker_fn

        other_mod = types.ModuleType("unrelated_utils")
        other_mod.__file__ = r"K:\ComfyUi\ComfyUI\ComfyUI\custom_nodes\other\utils.py"
        other_mod.cleanGPUUsedForce = MagicMock()

        with patch.dict(sys.modules, {"__test_easy_utils": fake_mod, "__test_other_utils": other_mod}):
            found = vram_monitor._find_easyuse_clean_fn()
            self.assertIs(found, marker_fn)

    def test_run_cleanup_rejects_when_queue_busy(self):
        fake_queue = MagicMock()
        fake_queue.get_current_queue_volatile.return_value = ([("running",)], [])
        with patch.object(vram_monitor, "_get_prompt_queue", return_value=fake_queue):
            status, payload = asyncio.run(vram_monitor._run_cleanup(deep=False))
            self.assertEqual(status, 409)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["reason"], "queue_busy")

    def test_run_cleanup_reuses_easyuse_and_deep_sets_flag(self):
        fake_queue = MagicMock()
        fake_queue.get_current_queue_volatile.return_value = ([], [])
        fake_queue.get_flags.return_value = {}
        easy_fn = MagicMock()

        with patch.object(vram_monitor, "_get_prompt_queue", return_value=fake_queue), \
             patch.object(vram_monitor, "_find_easyuse_clean_fn", return_value=easy_fn):
            status, payload = asyncio.run(vram_monitor._run_cleanup(deep=True))

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cleanup_source"], "easyuse")
        self.assertTrue(payload["deep"])
        self.assertTrue(payload["execution_cache_cleared"])
        fake_queue.set_flag.assert_called_once_with("free_memory", True)
        easy_fn.assert_called_once()

    def test_run_cleanup_uses_fallback_when_easyuse_missing(self):
        fake_queue = MagicMock()
        fake_queue.get_current_queue_volatile.return_value = ([], [])
        with patch.object(vram_monitor, "_get_prompt_queue", return_value=fake_queue), \
             patch.object(vram_monitor, "_find_easyuse_clean_fn", return_value=None), \
             patch.object(vram_monitor, "_fallback_clean_gpu") as fallback_fn:
            status, payload = asyncio.run(vram_monitor._run_cleanup(deep=False))

        self.assertEqual(status, 200)
        self.assertEqual(payload["cleanup_source"], "comfy_fallback")
        fallback_fn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
