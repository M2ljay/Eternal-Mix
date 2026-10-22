#!/usr/bin/env python3
"""Run integration fixtures in a real headless Chromium, without Steam installed."""
from __future__ import annotations
import argparse
import html
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', help='Path to Chromium/Chrome')
    parser.add_argument('--node', action='store_true', help='Run deterministic DOM fixtures in Node (not a real browser)')
    args = parser.parse_args()
    browser = args.browser or next((path for name in ('chromium','chromium-browser','google-chrome','chrome') if (path := shutil.which(name))), None)
    if not browser and not args.node:
        raise SystemExit('Chromium/Chrome not found; pass --browser with its full path.')
    bundle = (ROOT/'dist/index.js').read_text(encoding='utf-8')
    # rollup emits `let`, the esbuild build (scripts/build-esbuild.mjs) `var`.
    start = next(bundle.index(marker) for marker in ('let fullscreenChromeObservers =', 'var fullscreenChromeObservers =') if marker in bundle)
    end = bundle.index('function OceanLayer()', start)
    section = bundle[start:end]
    # esbuild reaches Steam's globals through module-level aliases
    # (import_ui7 = __toESM(require_ui()), ...). The fixture runs this section
    # on its own, so point any alias it uses at the global it stands for.
    alias_globals = {}
    for alias, module in re.findall(r'var (import_\w+) = __toESM\(require_(\w+)\(\)', bundle):
        source = re.search(r'var require_' + re.escape(module) + r' = __commonJS\(\{\s*"[^"]*"\(exports, module\) \{\s*module\.exports = (\w+);', bundle)
        if source and re.search(r'\b' + re.escape(alias) + r'\b', section):
            alias_globals[alias] = source.group(1)
    if alias_globals:
        # Read the global lazily: the fixture defines DFL after this section.
        section = ''.join(f'var {alias} = new Proxy({{}}, {{ get: (_, key) => {name}[key] }});\n' for alias, name in sorted(alias_globals.items())) + section
    sys.modules.setdefault('decky_plugin', types.ModuleType('decky_plugin'))
    spec = importlib.util.spec_from_file_location('np_browser_backend', ROOT/'main.py')
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    plugin = backend.Plugin.__new__(backend.Plugin)
    plugin.topbar_left = True
    scripts = {'left': plugin._topbar_injection_script('Track <test> & 日本', True),
               'changed': plugin._topbar_injection_script('New track', True),
               'off': plugin._topbar_injection_script('', False)}
    fixture=(ROOT/'scripts/steam-ui-fixture.html').read_text(encoding='utf-8')
    fixture=fixture.replace('/*__INTEGRATION__*/',section).replace('/*__TOPBAR__*/',json.dumps(scripts))
    with tempfile.TemporaryDirectory(prefix='now-playing-chromium-') as directory:
        page = Path(directory)/'test.html'
        page.write_text(fixture,encoding='utf-8')
        if args.node:
            result = subprocess.run(['node', str(ROOT/'scripts/validate-steam-dom.cjs'), str(page)], capture_output=True, text=True, timeout=12)
            print('Node DOM fixtures (not a live Steam/Chromium test)')
            print(result.stdout)
            if result.returncode:
                print(result.stderr)
                raise SystemExit(result.returncode)
            return
        result=subprocess.run([browser,'--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--no-first-run','--no-default-browser-check','--allow-file-access-from-files','--user-data-dir='+str(Path(directory)/'profile'),'--dump-dom','--virtual-time-budget=2000',page.as_uri()],capture_output=True,text=True,timeout=40)
        match=re.search(r'<pre id="results">(.*?)</pre>',result.stdout,re.S)
        report=html.unescape(match.group(1)) if match else 'Browser did not return a test result.\n'+result.stderr[-3000:]
        print(subprocess.run([browser,'--version'],capture_output=True,text=True).stdout.strip())
        print(report)
        if result.returncode or 'ALL 21 TESTS PASSED' not in report:
            raise SystemExit(1)

if __name__=='__main__':main()
