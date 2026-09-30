/**
 * Pure decision logic for when and what the guide should say aloud.
 *
 * Speech is driven by distance thresholds along the route line. The same
 * maneuver is never announced twice on the same threshold; a new maneuver
 * cancels any in-progress speech first.
 *
 * Thresholds are tuned for pedestrian walking on short urban legs
 * (between two stops is often 60–120 m). The classic 300/100 m split
 * is too wide: the next maneuver may be 80 m away, and 300 m would be the
 * end of the leg, not the beginning.
 *
 * Selected values:
 * - 80 m  — «далеко»: significant intersection or turn, enough time to prepare.
 * - 30 m  — «близко»: tight turn or alley, the window where Google maps says
 *              «сверните». 30 m is still reachable at walking pace in < 4 s.
 * - 10 m  — «очень близко»: the last verbal cue before the turn; on a 40 m leg
 *            it fires instead of 30 m, which is correct.
 * - 0 m   — «прибытие»: the tourist reached the destination (no more maneuvers).
 *
 * The module stays *pure* about language: it decides *which* threshold fired
 * and hands back the maneuver's own instruction (already in the route's
 * language, straight from Valhalla). The human phrase is assembled by the
 * panel through i18next — this module never hard-codes a sentence.
 */

export type FixQuality = 'unavailable' | 'waiting' | 'stale' | 'poor' | 'good';

/** A maneuver to be announced. */
export interface VoiceManeuver {
  key: string;
  instruction: string;
}

/** What the voice module decided to do. */
export type VoiceDecision =
  | { type: 'silent' }
  | {
      type: 'announce';
      /** Which distance band fired. */
      threshold: VoiceThreshold;
      /** Valhalla's instruction, already in the route's own language. */
      instruction: string;
      /** True for the 0 m band — the tourist has reached the destination. */
      arrival: boolean;
    };

/**
 * Distance bands at which a maneuver is announced.  Sorted ascending so that
 * `0` is checked first (most urgent) and the loop returns the *smallest*
 * threshold the distance satisfies — when the tourist is 30 m away the
 * 30 m band fires, not 80 m.
 */
export const VOICE_THRESHOLDS_M = [0, 10, 30, 80] as const;

/** One of the distance bands above, as a type. */
export type VoiceThreshold = (typeof VOICE_THRESHOLDS_M)[number];

/** Which thresholds have already been spoken for a given maneuver. */
export type SpokenThresholds = Map<string, Set<VoiceThreshold>>;

/** Merge a new spoken threshold into the map. */
export const markSpoken = (
  spoken: SpokenThresholds,
  maneuverKey: string,
  threshold: VoiceThreshold
): SpokenThresholds => {
  const next = new Map(spoken);
  const existing = next.get(maneuverKey);
  if (existing) {
    const updated = new Set(existing);
    updated.add(threshold);
    next.set(maneuverKey, updated);
  } else {
    next.set(maneuverKey, new Set([threshold]));
  }
  return next;
};

/**
 * Decide what (if anything) the guide should say right now.
 *
 * Returns `VoiceDecision` — data, not a sentence — so the caller can phrase it
 * in the interface language and attach metadata (distance) without
 * re-computing anything.
 */
export const decideVoice = (params: {
  maneuver: VoiceManeuver | null;
  /** Distance in metres from the tourist's frozen position to the maneuver. */
  distanceM: number | null;
  /** Whether the fix is trustworthy enough for confident announcements. */
  quality: FixQuality;
  /** Whether the tourist is still on the route line. */
  offRoute: boolean;
  /** Mute flag from the UI. */
  muted: boolean;
  /** What has already been spoken. */
  spoken: SpokenThresholds;
}): VoiceDecision => {
  const { maneuver, distanceM, quality, offRoute, muted, spoken } = params;

  // Always silence when:
  // - muted in the UI
  // - GPS cannot be trusted
  // - the tourist has left the route (the panel is already showing re-plan)
  if (muted || quality !== 'good' || offRoute) {
    return { type: 'silent' };
  }

  if (!maneuver) {
    return { type: 'silent' };
  }

  if (distanceM == null) {
    return { type: 'silent' };
  }

  // Find the first threshold this maneuver has not yet crossed.
  // VOICE_THRESHOLDS_M is sorted ascending.
  for (const threshold of VOICE_THRESHOLDS_M) {
    const alreadySpoken = spoken.get(maneuver.key)?.has(threshold) ?? false;
    if (distanceM <= threshold && !alreadySpoken) {
      return {
        type: 'announce',
        threshold,
        instruction: maneuver.instruction,
        // The 0 m band is the arrival: Valhalla's final instruction is often
        // empty there, so the panel falls back to its own «прибыли» phrase.
        arrival: threshold === 0,
      };
    }
  }

  return { type: 'silent' };
};

/** Whether two maneuvers are the same (key is the stable identity). */
export const isNewManeuver = (
  prev: VoiceManeuver | null,
  next: VoiceManeuver | null
): boolean => {
  if (prev === null || next === null) return true;
  return prev.key !== next.key;
};

// ── SpeechSynthesis wrapper ────────────────────────────────────────────────────

/** The real browser type, used only as a return type. */
type SpeechSynthesisInstance = typeof window extends {
  speechSynthesis: infer S;
}
  ? S
  : never;

const getSpeech = (): SpeechSynthesisInstance | undefined => {
  if (typeof window === 'undefined') return undefined;
  return window.speechSynthesis;
};

/** Whether the browser supports speech synthesis. */
export const isSpeechAvailable = (): boolean => {
  return getSpeech() != null;
};

/**
 * Speak `text` in the given language.
 * Cancels any in-progress speech first so the new utterance is immediate,
 * never queued.
 *
 * A real `SpeechSynthesisUtterance` is required: `speechSynthesis.speak()` in a
 * browser throws `TypeError` on anything else. Where the constructor does not
 * exist (a bare test environment, an engine without Web Speech) this is a
 * silent no-op rather than a crash inside the guide's effect.
 */
export const speak = (text: string, lang: string = 'ru-RU'): void => {
  const ss = getSpeech();
  if (!ss) return;
  const Utterance =
    typeof window === 'undefined' ? undefined : window.SpeechSynthesisUtterance;
  if (typeof Utterance !== 'function') return;

  ss.cancel();
  const utterance = new Utterance(text);
  utterance.lang = lang;
  ss.speak(utterance);
};

/** Stop any in-progress speech immediately. */
export const cancelSpeech = (): void => {
  const ss = getSpeech();
  if (!ss) return;
  ss.cancel();
};
