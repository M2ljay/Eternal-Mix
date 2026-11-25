Vol. 1 (1.0.1)

- Safer updates: before an update is handed to Decky, Awesome Mix now stops its helper programs (such as MediaBridge) so Windows can replace every file. Previously a file still in use could stop the update half-way and leave the plugin unable to start.
- Install check: Awesome Mix checks its own files when it starts. If the last update didn't finish copying, it says so and offers a Reinstall button that downloads and installs the current version again. This works even when part of the plugin is missing.
- The update download is now refused unless every file inside it matches the file list shipped with it.
