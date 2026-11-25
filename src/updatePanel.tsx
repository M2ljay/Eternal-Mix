// Settings section for the self-update channel (mix_update.py).
//
// The backend downloads the newest build and refuses it unless its size, SHA-256
// and layout match update.json, stops its helper programs (installHandoff.ts),
// and the verified file is then handed to Decky Loader's own installer (the route its store uses):
// Decky asks once more, checks the SHA-256 again, replaces the "Awesome Mix"
// folder and reloads the plugin. Settings live in homebrew/settings/Awesome Mix,
// outside the plugin folder, and are kept.
import { ConfirmModal, DialogButton, showModal } from "@decky/ui";
import { CSSProperties, useEffect, useState } from "react";
import { FaDownload, FaSyncAlt } from "react-icons/fa";
import { formatTranslation, getTranslations } from "./i18n";
import * as python from "./python";
import { deckyRouter, handOffToDecky } from "./installHandoff";
import { formatMegabytes, updateReasonKey } from "./updateText";
import { PLUGIN_VERSION, PLUGIN_VOLUME } from "./version";
import { volumeLabel } from "./volume";

type Status = { text: string; error?: boolean };

export function UpdateSettingsPanel(props: {
  titleStyle?: CSSProperties;
  buttonStyle?: CSSProperties;
  contentStyle?: CSSProperties;
}) {
  const t = getTranslations("update");
  const [check, setCheck] = useState<python.PluginUpdateCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const router = deckyRouter();

  const failureText = (failure: python.PluginUpdateFailure) => {
    const key = updateReasonKey(failure.reason);
    return formatTranslation(String(t[key]), { error: failure.message });
  };

  async function refresh(force: boolean) {
    setBusy(true);
    if (force) setStatus({ text: t.checking });
    try {
      const result = await python.checkPluginUpdate(force);
      setCheck(result);
      if (result.ok === false) setStatus({ text: failureText(result), error: true });
      else setStatus(force && !result.updateAvailable ? { text: t.upToDate } : null);
    } catch (error: any) {
      setStatus({ text: formatTranslation(t.errorUnknown, { error: error?.message ?? String(error) }), error: true });
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh(false);
  }, []);

  async function install() {
    if (!router) return;
    setBusy(true);
    setStatus({ text: t.downloading });
    try {
      const prepared = await python.preparePluginUpdate();
      if (prepared.ok === false) {
        setStatus({ text: failureText(prepared), error: true });
        return;
      }
      const handed = await handOffToDecky(router, prepared);
      if (handed.ok === false) {
        setStatus({ text: failureText(handed), error: true });
        return;
      }
      setStatus({ text: t.handedOff });
    } catch (error: any) {
      setStatus({ text: formatTranslation(t.errorDecky, { error: error?.message ?? String(error) }), error: true });
    } finally {
      setBusy(false);
    }
  }

  const offered = check?.ok && check.updateAvailable && check.version ? check : null;
  const offeredVolume = offered ? volumeLabel(offered.version ?? "") : "";
  const offeredSize = offered ? formatMegabytes(offered.size) : "";

  function confirmInstall() {
    if (!offered) return;
    showModal(
      <ConfirmModal
        strTitle={t.confirmTitle}
        strDescription={formatTranslation(t.confirmBody, { volume: offeredVolume, version: offered.version ?? "", size: offeredSize })}
        strOKButtonText={t.confirmInstall}
        strCancelButtonText={t.cancel}
        onOK={() => void install()}
      />,
    );
  }

  const buttonStyle: CSSProperties = { width: "100%", minWidth: "100%", height: 46, minHeight: 46, marginBottom: 8, padding: 0, ...props.buttonStyle };
  const contentStyle: CSSProperties = { width: "100%", display: "flex", alignItems: "center", gap: 10, padding: "0 12px", boxSizing: "border-box", textAlign: "left", ...props.contentStyle };
  const noteStyle: CSSProperties = { width: "100%", margin: "0 0 8px", color: "rgba(255,255,255,0.62)", fontSize: "0.82em", lineHeight: 1.35, whiteSpace: "pre-line" };

  return (
    <div className="npUpdateSettingsPanel" style={{ width: "100%" }}>
      <div style={props.titleStyle}>{t.title}</div>
      <div style={noteStyle}>
        {formatTranslation(t.installed, { volume: PLUGIN_VOLUME, version: PLUGIN_VERSION })}
      </div>
      {offered && (
        <>
          <div style={{ ...noteStyle, color: "#fff" }}>
            {formatTranslation(t.available, { volume: offeredVolume, version: offered.version ?? "" })}
          </div>
          {offered.notes ? <div style={{ ...noteStyle, maxHeight: 160, overflowY: "auto" }}>{offered.notes}</div> : null}
          <DialogButton style={buttonStyle} disabled={busy || !router} onClick={confirmInstall}>
            <span style={contentStyle}>
              <FaDownload />
              <span>{formatTranslation(t.installButton, { volume: offeredVolume, size: offeredSize })}</span>
            </span>
          </DialogButton>
          {!router ? <div style={noteStyle}>{t.unsupportedLoader}</div> : null}
        </>
      )}
      <DialogButton style={buttonStyle} disabled={busy} onClick={() => void refresh(true)}>
        <span style={contentStyle}>
          <FaSyncAlt className={busy ? "npRestartSpin" : undefined} />
          <span>{t.checkForUpdates}</span>
        </span>
      </DialogButton>
      {status ? (
        <div role="status" style={{ ...noteStyle, color: status.error ? "#ffb4a8" : noteStyle.color }}>
          {status.text}
        </div>
      ) : null}
    </div>
  );
}
