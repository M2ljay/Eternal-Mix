import { localAudioPlayer } from "./localAudio";

export type VisualLevels = { energy: number; bass: number; mid: number; treble: number };

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

/**
 * Musical fallback pulse so the visualizers never freeze, even when no live
 * measurement is available (analyser blocked, or a Windows media session such
 * as Apple Music, whose audio the plugin cannot measure).
 */
function syntheticLevels(seconds: number, intensity: number): VisualLevels {
  const beat = Math.pow(Math.max(0, Math.sin(seconds * Math.PI * 2 * 1.02)), 3);
  const half = Math.pow(Math.max(0, Math.sin(seconds * Math.PI * 2 * 0.51 + 0.6)), 2);
  const bass = (0.34 + beat * 0.5) * intensity;
  const mid = (0.3 + half * 0.28 + Math.sin(seconds * 2.3) * 0.08) * intensity;
  const treble = (0.24 + Math.abs(Math.sin(seconds * 5.2 + 1.3)) * 0.26) * intensity;
  return {
    bass: clamp01(bass),
    mid: clamp01(mid),
    treble: clamp01(treble),
    energy: clamp01(bass * 0.5 + mid * 0.34 + treble * 0.16),
  };
}

// Per-band response weighting, in order of prominence: bass detection is
// boosted 20%; treble reacts at 70% of bass; mid at 30% of bass.
const BASS_GAIN = 1.2;
const TREBLE_GAIN = BASS_GAIN * 0.7;
const MID_GAIN = BASS_GAIN * 0.3;
class AdaptiveSourceGain {
  private peak = 0.18;
  private gain = 1;

  apply(levels: VisualLevels): VisualLevels {
    const inputPeak = Math.max(levels.energy, levels.bass, levels.mid, levels.treble);
    if (inputPeak > 0.004) {
      // Follow louder transients quickly, then decay slowly so quiet masters get
      // compensated without pumping every beat or clipping louder tracks.
      this.peak = inputPeak > this.peak
        ? this.peak * 0.72 + inputPeak * 0.28
        : Math.max(0.055, this.peak * 0.996);
    }
    // Target a ~0.72 output peak so the local/YouTube Music analyser drives the
    // bands up to ~1.0. The old 0.48 target left them visibly weaker.
    const targetGain = Math.min(6, Math.max(1, 0.72 / Math.max(0.055, this.peak)));
    this.gain += (targetGain - this.gain) * (targetGain > this.gain ? 0.08 : 0.035);
    return {
      bass: clamp01(levels.bass * this.gain),
      mid: clamp01(levels.mid * this.gain),
      treble: clamp01(levels.treble * this.gain),
      energy: clamp01(levels.energy * this.gain),
    };
  }

  relax() {
    this.peak = this.peak * 0.99 + 0.18 * 0.01;
    this.gain += (1 - this.gain) * 0.03;
  }
}

// One adaptive gain for every in-browser source (local files AND YouTube Music).
// Local music previously used the raw analyser bands with no compensation, which
// made its effects look weak.
const localAudioGain = new AdaptiveSourceGain();

function weighBands(levels: VisualLevels): VisualLevels {
  const bass = clamp01(levels.bass * BASS_GAIN);
  const mid = clamp01(levels.mid * MID_GAIN);
  const treble = clamp01(levels.treble * TREBLE_GAIN);
  return { bass, mid, treble, energy: clamp01(bass * 0.5 + mid * 0.34 + treble * 0.16) };
}

/**
 * Per-frame levels for a fullscreen visualizer.
 *
 * Priority: real Web Audio bands (local/YouTube Music), then the synthetic
 * pulse — strong while playing, gentle breathing while paused. Every path is passed through the
 * band weighting so the response order (bass > treble > mid) is consistent.
 */
export function readVisualizerLevels(useLocalAudio: boolean, isPlayingHint: boolean, seconds: number): VisualLevels {
  if (useLocalAudio) {
    const measured = localAudioPlayer.getAudioLevels();
    // Normalise the analyser loudness with the adaptive gain, then derive the
    // bands from the resulting energy (raw analyser bands read low).
    const gained = localAudioGain.apply(measured);
    if (gained.energy > 0.006) {
      const shape = syntheticLevels(seconds, 1);
      return weighBands({
        energy: clamp01(gained.energy),
        bass: clamp01(gained.energy * (0.75 + shape.bass * 0.5)),
        mid: clamp01(gained.energy * (0.7 + shape.mid * 0.5)),
        treble: clamp01(gained.energy * (0.6 + shape.treble * 0.55)),
      });
    }
    const playing = localAudioPlayer.getSnapshot().status === "Playing" || isPlayingHint;
    return weighBands(syntheticLevels(seconds, playing ? 0.8 : 0.16));
  }
  // Windows media sessions (Apple Music) cannot be measured: let the local
  // adaptive gain drift back to neutral and show the synthetic pulse.
  localAudioGain.relax();
  return weighBands(syntheticLevels(seconds, isPlayingHint ? 0.8 : 0.16));
}
