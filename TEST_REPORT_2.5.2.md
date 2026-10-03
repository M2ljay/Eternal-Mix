# Now Playing 2.5.2 validation

Validated on Windows on 2026-10-03 without controlling live playback or changing Steam navigation.

The five production top-bar scheduling tests pass. They verify bounded refresh while stopped, immediate playback and pause transitions, active badge recovery, sleep while disabled, prompt settings wakeup and cancellation. The full verification command passes translations, type checking, frozen-runtime regressions in three modes, loopback HTTP lifecycle, Steam bundle parity, 21 DOM integration cases and bundle syntax.

Read-only RPC against the installed plugin returns active source Spotify, playback status Stopped, no active Windows media players and top-bar enabled. The existing plugin event listeners are retained. The installed frontend reports version 2.5.2. Installed main.py, package.json and dist/index.js match the reviewed repository bytes. The installed .playhub-release.json still says 2.5.1; this deployment marker needs reconciliation by the task that owns installation. The repository release marker is 2.5.2.

No playback command, settings setter, controller input, plugin reload or game launch was used in this validation. Live play, pause and resume remains a separate root-coordinated test; the fixture transitions establish scheduler behavior without claiming that live playback was exercised.

Commands:

```powershell
python -B -m unittest discover -s tests -v
npm run verify
```

Live API result: F:\Playhub\Plugin\Playhub\.local\red-code\now-playing-backend-live-read-20261003.json.
