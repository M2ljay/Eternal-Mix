// Install check banner (mix_install_check.py, main.py).
//
// When the last update did not finish copying (a file in use stopped Decky's
// delete or extract half-way), the backend reports which files are missing or
// damaged. The banner says so in plain words and offers to reinstall the
// current version from the release channel. In safe mode (the backend itself
// could not load) the banner is all the panel shows.
import { DialogButton, PanelSection, PanelSectionRow } from "@decky/ui";
import { CSSProperties, ReactNode, useEffect, useState } from "react";
import { FaRedo } from "react-icons/fa";
import { formatTranslation, getTranslations } from "./i18n";
import { deckyRouter, handOffToDecky } from "./installHandoff";
import { installCheckPollDelay, shouldShowInstallBanner } from "./installCheckText";
import * as python from "./python";
import { updateReasonKey } from "./updateText";
import { PLUGIN_DISPLAY_NAME } from "./version";
import { volumeLabel } from "./volume";

function useInstallCheck(): python.InstallCheck | null {
  const [check, setCheck] = useState<python.InstallCheck | null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let polls = 0;
    const poll = async () => {
      try {
        const result = await python.getInstallCheck();
        if (cancelled) return;
        setCheck(result);
        const delay = installCheckPollDelay(result, ++polls);
        if (delay !== null) timer = setTimeout(() => void poll(), delay);
      } catch {
        // A backend without the install check (or not running) shows the normal panel.
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, []);
  return check;
}

type Status = { text: string; error?: boolean };

function InstallCheckBanner(props: { check: python.InstallCheck }) {
  const t = getTranslations("installCheck");
  const tu = getTranslations("update");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const router = deckyRouter();
  const volume = volumeLabel(props.check.version);

  const failureText = (failure: python.PluginUpdateFailure) =>
    formatTranslation(String(tu[updateReasonKey(failure.reason)]), { error: failure.message });

  async function reinstall() {
    if (!router) return;
    setBusy(true);
    setStatus({ text: t.downloading });
    try {
      const prepared = await python.preparePluginReinstall();
      if (prepared.ok === false) {
        setStatus({ text: failureText(prepared), error: true });
        return;
      }
      const handed = await handOffToDecky(router, prepared);
      if (handed.ok === false) {
        setStatus({ text: failureText(handed), error: true });
        return;
      }
      setStatus({ text: tu.handedOff });
    } catch (error: any) {
      setStatus({ text: formatTranslation(tu.errorDecky, { error: error?.message ?? String(error) }), error: true });
    } finally {
      setBusy(false);
    }
  }

  const box: CSSProperties = { width: "100%", padding: "10px 12px", boxSizing: "border-box", borderRadius: 6, background: "var(--mix-surface, rgba(255,120,90,0.14))", border: "1px solid var(--mix-error, rgba(255,140,110,0.45))", color: "#fff" };
  const note: CSSProperties = { margin: "6px 0 0", fontSize: "0.85em", lineHeight: 1.35, color: "var(--mix-steel, rgba(255,255,255,0.75))" };
  const button: CSSProperties = { width: "100%", minWidth: "100%", height: 46, minHeight: 46, marginTop: 10, padding: 0 };
  const content: CSSProperties = { width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 10 };

  return (
    <div className="npInstallCheckBanner" role="alert" style={box}>
      <div style={{ fontWeight: 600, lineHeight: 1.35 }}>{formatTranslation(t.body, { name: PLUGIN_DISPLAY_NAME })}</div>
      {props.check.problemCount > 0 ? <div style={note}>{formatTranslation(t.problems, { count: props.check.problemCount })}</div> : null}
      {props.check.safeMode ? <div style={note}>{formatTranslation(t.safeMode, { name: PLUGIN_DISPLAY_NAME })}</div> : null}
      {router ? (
        <DialogButton style={button} disabled={busy} onClick={() => void reinstall()}>
          <span style={content}>
            <FaRedo />
            <span>{formatTranslation(t.reinstall, { volume })}</span>
          </span>
        </DialogButton>
      ) : (
        <div style={note}>{t.manual}</div>
      )}
      {status ? <div role="status" style={{ ...note, ...(status.error ? { color: "var(--mix-error-text, #ffb4a8)", boxShadow: "var(--mix-error-bar, none)", paddingLeft: "var(--mix-error-pad, 0)" } : {}) }}>{status.text}</div> : null}
    </div>
  );
}

/** QAM content wrapper: the banner above the normal panel, or alone in safe mode. */
export function InstallCheckGate(props: { children: ReactNode }) {
  const check = useInstallCheck();
  const banner = check && shouldShowInstallBanner(check) ? (
    <PanelSection>
      <PanelSectionRow>
        <InstallCheckBanner check={check} />
      </PanelSectionRow>
    </PanelSection>
  ) : null;
  if (check?.safeMode) return banner;
  return (
    <>
      {banner}
      {props.children}
    </>
  );
}
