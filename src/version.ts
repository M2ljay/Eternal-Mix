import packageInfo from "../package.json";
import { volumeLabel } from "./volume";

export const PLUGIN_DISPLAY_NAME = "Awesome Mix";
export const PLUGIN_VERSION: string = packageInfo.version;
export const PLUGIN_VOLUME = volumeLabel(PLUGIN_VERSION);
export const PLUGIN_CREDITS = "Based on Now Playing by Andrea Sgarro (LoZazaMastro)";
