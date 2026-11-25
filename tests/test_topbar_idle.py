"""Production topbar scheduling checks without Decky, helpers or Steam."""
import ast
import asyncio
import json
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / 'mix_backend.py').read_text(encoding='utf-8'))
plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Plugin')
methods = {'_refresh_topbar_once', '_topbar_loop', 'set_topbar_enabled', 'set_topbar_left'}
selected = [node for node in plugin.body if isinstance(node, ast.AsyncFunctionDef) and node.name in methods]
constants = [node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id.startswith('TOPBAR_') and target.id.endswith('_SECONDS') for target in node.targets)]
clock = types.SimpleNamespace(value=0.0)
environment = {'asyncio': asyncio, 'json': json, 'time': types.SimpleNamespace(monotonic=lambda: clock.value)}
exec(compile(ast.fix_missing_locations(ast.Module(body=constants + [ast.ClassDef(name='Harness', bases=[], keywords=[], body=selected, decorator_list=[])], type_ignores=[])), str(ROOT / 'mix_backend.py'), 'exec'), environment)

class TopbarIdleTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, enabled=True):
        obj = environment['Harness']()
        obj.topbar_enabled = enabled
        obj.topbar_left = False
        obj._topbar_settings_changed = asyncio.Event()
        obj._topbar_last_signature = ''
        obj._topbar_last_injected_at = 0.0
        obj._realtime_executor = obj._topbar_executor = None
        obj.state = {'playing': False, 'title': '', 'service': 'youtubeMusic'}
        obj.injections = []
        obj.state_reads = 0
        def read():
            obj.state_reads += 1
            return dict(obj.state)
        obj._current_topbar_state = read
        obj._format_topbar_label = lambda title: title
        obj._inject_topbar_badge_with_service = lambda *args: obj.injections.append(args)
        obj._inject_topbar_badge = lambda *args: obj.injections.append(args)
        obj._save_topbar_setting = lambda enabled: None
        obj._log = lambda message: None
        async def run(_executor, function, *args):
            return function(*args)
        obj._run_in_executor = run
        clock.value = 0.0
        return obj

    async def test_quiet_playback_keeps_detection_but_bounds_cdp_work(self):
        obj = self.make_plugin()
        for tick in range(61):
            clock.value = tick * 0.75
            await obj._refresh_topbar_once()
        self.assertEqual(obj.state_reads, 61)
        self.assertEqual(len(obj.injections), 2)  # Initial cleanup, then 30 seconds.

    async def test_active_badge_still_repairs_and_pause_play_resume_are_immediate(self):
        obj = self.make_plugin()
        obj.state.update(playing=True, title='Track one')
        for tick in range(13):
            clock.value = tick * 0.75
            await obj._refresh_topbar_once()
        self.assertEqual(len(obj.injections), 5)
        obj.state['playing'] = False
        clock.value += 0.01
        await obj._refresh_topbar_once()
        self.assertFalse(obj.injections[-1][1])
        obj.state.update(playing=True, title='Track two')
        clock.value += 0.01
        await obj._refresh_topbar_once()
        self.assertEqual(obj.injections[-1][:2], ('Track two', True))
        self.assertEqual(len(obj.injections), 7)

    async def test_disabled_loop_sleeps_until_settings_and_cancels_cleanly(self):
        obj = self.make_plugin(enabled=False)
        task = asyncio.create_task(obj._topbar_loop())
        await asyncio.sleep(0)
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(len(obj.injections), 1)
        self.assertEqual(obj.state_reads, 0)
        obj.state.update(playing=True, title='New playback')
        await obj.set_topbar_enabled(True)
        await asyncio.sleep(0)
        self.assertEqual(obj.injections[-1][:2], ('New playback', True))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_position_change_forces_immediate_refresh_in_quiet_state(self):
        obj = self.make_plugin()
        await obj._refresh_topbar_once()
        await obj.set_topbar_left(True)
        self.assertEqual(len(obj.injections), 2)
        self.assertTrue(obj.injections[-1][3])

    async def test_disabled_unchanged_state_never_reinjects_on_elapsed_time(self):
        obj = self.make_plugin(enabled=False)
        await obj._refresh_topbar_once()
        clock.value = 3600.0
        await obj._refresh_topbar_once()
        self.assertEqual(len(obj.injections), 1)
        self.assertEqual(obj.state_reads, 0)

if __name__ == '__main__':
    unittest.main()
