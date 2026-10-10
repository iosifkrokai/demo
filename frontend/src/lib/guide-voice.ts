/** Pure decision logic for when and what the guide should say aloud. */

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

export type TriggeredVoiceDecision =
  | { type: 'silent' }
  | {
      type: 'announce';
      distanceM: number;
      instruction: string;
    };

const canSpeak = ({
  quality,
  offRoute,
  muted,
}: {
  quality: FixQuality;
  offRoute: boolean;
  muted: boolean;
}) => !muted && quality === 'good' && !offRoute;

/** Accept an announcement whose distance trigger was selected by the navigation engine rather than by this module's legacy distance thresholds. */
export const decideTriggeredVoice = (params: {
  instruction: string;
  distanceM: number;
  quality: FixQuality;
  offRoute: boolean;
  muted: boolean;
}): TriggeredVoiceDecision => {
  const { instruction, distanceM } = params;
  if (!canSpeak(params) || !Number.isFinite(distanceM) || distanceM < 0) {
    return { type: 'silent' };
  }
  return { type: 'announce', distanceM, instruction };
};

/** Distance bands at which a maneuver is announced. */
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

/** Decide what (if anything) the guide should say right now. */
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
  const { maneuver, distanceM, spoken } = params;

  if (!canSpeak(params)) {
    return { type: 'silent' };
  }

  if (!maneuver) {
    return { type: 'silent' };
  }

  if (distanceM == null) {
    return { type: 'silent' };
  }

  for (const threshold of VOICE_THRESHOLDS_M) {
    const alreadySpoken = spoken.get(maneuver.key)?.has(threshold) ?? false;
    if (distanceM <= threshold && !alreadySpoken) {
      return {
        type: 'announce',
        threshold,
        instruction: maneuver.instruction,
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

/** Speak `text` in the given language. */
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
