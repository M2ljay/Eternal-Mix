#!/usr/bin/env python3
"""Real loopback HTTP checks; no online service or Windows APIs are exercised."""
from __future__ import annotations
import importlib.util
import socket
import sys
import time
import types
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.modules.setdefault('decky_plugin',types.ModuleType('decky_plugin'))
spec=importlib.util.spec_from_file_location('np_loopback_backend',ROOT/'main.py')
backend=importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

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

def test_oauth(playback):
    plugin=backend.Plugin.__new__(backend.Plugin)
    prefix='_spotify_playback_auth' if playback else '_spotify_auth'
    setattr(plugin, prefix+'_server',None)
    setattr(plugin, prefix+'_thread',None)
    setattr(plugin, 'spotify_playback_redirect_port' if playback else 'spotify_redirect_port',0)
    setattr(plugin,'_spotify_handle_playback_auth_callback' if playback else '_spotify_handle_auth_callback',lambda query:(True,'<hello> & safe'))
    start=getattr(plugin,'_start'+prefix+'_server')
    stop=getattr(plugin,'_stop'+prefix+'_server')
    start()
    listener=getattr(plugin,prefix+'_server')
    thread=getattr(plugin,prefix+'_thread')
    port=listener.server_address[1]
    try:
        path='/login' if playback else '/callback'
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}?state=test',timeout=3) as reply:
            body=reply.read().decode('utf-8')
            assert reply.status==200 and '&lt;hello&gt; &amp; safe' in body
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and (thread.is_alive() or listener.socket.fileno()!=-1):
            time.sleep(0.01)
        assert not thread.is_alive() and listener.socket.fileno()==-1,'OAuth listener was not closed'
        assert getattr(plugin,prefix+'_server') is None
        stop()
        # Reopen the same port, then explicitly unload before a callback arrives.
        setattr(plugin,'spotify_playback_redirect_port' if playback else 'spotify_redirect_port',port)
        start()
        replacement=getattr(plugin,prefix+'_server')
        stop()
        assert replacement.socket.fileno()==-1
    finally:
        stop()
    print(f'PASS: {path} callback, escaped response, port release and same-port restart')

if __name__=='__main__':
    test_idle_stream_stop()
    test_oauth(False)
    test_oauth(True)
    print('Loopback HTTP lifecycle suite OK (3 tests).')
