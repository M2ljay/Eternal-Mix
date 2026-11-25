// Hands a verified installer to Decky Loader, shared by the update panel and the install check.
//
// Decky v3.3.1 on Windows stops the backend with TerminateProcess (our _unload
// never runs), deletes the plugin folder and extracts the zip over it. A helper
// program that is still running keeps its files open and stops either step
// half-way, so the backend first stops every helper (prepare_for_plugin_install).
import * as python from "./python";
import { DECKY_INSTALL_TYPE_UPDATE } from "./updateText";
import { PLUGIN_DISPLAY_NAME } from "./version";

type DeckyRouter = { call(route: string, ...args: unknown[]): Promise<unknown> };

/** Decky Loader's websocket router (window.DeckyBackend); not part of @decky/api. */
export function deckyRouter(): DeckyRouter | undefined {
  const router = (globalThis as unknown as { DeckyBackend?: DeckyRouter }).DeckyBackend;
  return typeof router?.call === "function" ? router : undefined;
}

export type HandoffResult = { ok: true } | python.PluginUpdateFailure;

export async function handOffToDecky(router: DeckyRouter, prepared: { artifact: string; version: string; sha256: string }): Promise<HandoffResult> {
  const ready = await python.prepareForPluginInstall();
  if (ready.ok === false) return ready;
  // The name must be plugin.json's ("Awesome Mix"): Decky finds the installed
  // folder by it. Another name would install a second copy beside this one.
  await router.call("utilities/install_plugin", prepared.artifact, PLUGIN_DISPLAY_NAME, prepared.version, prepared.sha256, DECKY_INSTALL_TYPE_UPDATE);
  return { ok: true };
}
