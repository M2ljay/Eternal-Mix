#!/usr/bin/env python3
"""Real loopback HTTP checks; no online service or Windows APIs are exercised."""
from __future__ import annotations
import importlib.util
import socket
import sys
import time
import types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.modules.setdefault('decky_plugin',types.ModuleType('decky_plugin'))
spec=importlib.util.spec_from_file_location('np_loopback_entry',ROOT/'main.py')
entry=importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
assert entry.BACKEND_ERROR=='' and entry._backend is not None,entry.BACKEND_ERROR
backend=entry._backend

def test_idle_stream_stop():
    stream=backend.LocalMusicStreamServer(lambda _: '',lambda *_: '',lambda _: None)
    stream.start()
    listener=stream._server
    thread=stream._thread
    with socket.create_connection(listener.server_address,timeout=2) as idle:
        time.sleep(0.03)
        started=time.monotonic()
        stream.stop()
        assert time.monotonic()-started < 2.0, 'Idle CEF socket blocked shutdown'
    assert listener.socket.fileno()==-1 and not thread.is_alive()
    assert stream.base_url=='' and stream.port==0
    stream.stop()  # idempotent
    stream.start()
    try:
        assert stream.port > 0 and stream._thread.is_alive()
    finally:
        stream.stop()
    print('PASS: idle audio connection, bounded shutdown, closed listener and restart')

if __name__=='__main__':
    test_idle_stream_stop()
    print('Loopback HTTP lifecycle suite OK (1 test).')
