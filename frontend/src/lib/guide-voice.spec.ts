import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  decideTriggeredVoice,
  decideVoice,
  isNewManeuver,
  markSpoken,
  cancelSpeech,
  speak,
  isSpeechAvailable,
  type SpokenThresholds,
  type VoiceManeuver,
  VOICE_THRESHOLDS_M,
} from './guide-voice';

const maneuver = (
  key: string,
  instruction = 'Поверните направо'
): VoiceManeuver => ({
  key,
  instruction,
});

const good = () => ({ quality: 'good' as const });
const poor = () => ({ quality: 'poor' as const });
const stale = () => ({ quality: 'stale' as const });

const spokenNone: SpokenThresholds = new Map();
const muted = () => ({ muted: true });

const mockSpeechSynthesis = {
  speak: vi.fn(),
  cancel: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  getVoices: vi.fn(() => []),
  pending: false,
  speaking: false,
  paused: false,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn(),
};

/** A real-enough utterance; the wrapper must construct one, not pass a plain object. */
class MockSpeechSynthesisUtterance {
  text: string;
  lang = '';
  constructor(text: string) {
    this.text = text;
  }
}

vi.stubGlobal('speechSynthesis', mockSpeechSynthesis);
vi.stubGlobal('SpeechSynthesisUtterance', MockSpeechSynthesisUtterance);

describe('decideVoice', () => {
  it('silences when muted', () => {
    expect(
      decideVoice({
        maneuver: maneuver('1'),
        distanceM: 80,
        ...good(),
        ...muted(),
        offRoute: false,
        spoken: spokenNone,
      })
    ).toEqual({ type: 'silent' });
  });

  it('silences when fix is poor', () => {
    expect(
      decideVoice({
        maneuver: maneuver('1'),
        distanceM: 80,
        ...poor(),
        offRoute: false,
        muted: false,
        spoken: spokenNone,
      })
    ).toEqual({ type: 'silent' });
  });

  it('silences when fix is stale', () => {
    expect(
      decideVoice({
        maneuver: maneuver('1'),
        distanceM: 80,
        ...stale(),
        offRoute: false,
        muted: false,
        spoken: spokenNone,
      })
    ).toEqual({ type: 'silent' });
  });

  it('silences when off route', () => {
    expect(
      decideVoice({
        maneuver: maneuver('1'),
        distanceM: 80,
        ...good(),
        offRoute: true,
        muted: false,
        spoken: spokenNone,
      })
    ).toEqual({ type: 'silent' });
  });

  it('silences when no maneuver', () => {
    expect(
      decideVoice({
        maneuver: null,
        distanceM: 80,
        ...good(),
        offRoute: false,
        muted: false,
        spoken: spokenNone,
      })
    ).toEqual({ type: 'silent' });
  });

  it('silences when distance is unknown', () => {
    expect(
      decideVoice({
        maneuver: maneuver('1'),
        distanceM: null,
        ...good(),
        offRoute: false,
        muted: false,
        spoken: spokenNone,
      })
    ).toEqual({ type: 'silent' });
  });

  it('announces at 80 m', () => {
    const result = decideVoice({
      maneuver: maneuver('1', 'Поверните направо'),
      distanceM: 80,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenNone,
    });
    expect(result).toMatchObject({
      type: 'announce',
      threshold: 80,
      instruction: 'Поверните направо',
      arrival: false,
    });
  });

  it('announces at 30 m', () => {
    const result = decideVoice({
      maneuver: maneuver('1', 'Поверните налево'),
      distanceM: 30,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenNone,
    });
    expect(result).toMatchObject({
      type: 'announce',
      threshold: 30,
      instruction: 'Поверните налево',
      arrival: false,
    });
  });

  it('announces at 10 m', () => {
    const result = decideVoice({
      maneuver: maneuver('1', 'Сверните в переулок'),
      distanceM: 10,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenNone,
    });
    expect(result).toMatchObject({
      type: 'announce',
      threshold: 10,
      instruction: 'Сверните в переулок',
      arrival: false,
    });
  });

  it('announces arrival at 0 m', () => {
    const result = decideVoice({
      maneuver: maneuver('1', 'Большая улица'),
      distanceM: 0,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenNone,
    });
    // The arrival band keeps the destination name; the panel adds no distance.
    expect(result).toMatchObject({
      type: 'announce',
      threshold: 0,
      instruction: 'Большая улица',
      arrival: true,
    });
  });

  it('flags arrival with an empty instruction for the panel to phrase', () => {
    const result = decideVoice({
      maneuver: maneuver('1', ''),
      distanceM: 0,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenNone,
    });
    expect(result).toMatchObject({
      type: 'announce',
      threshold: 0,
      instruction: '',
      arrival: true,
    });
  });

  it('does not announce the same threshold twice for the same maneuver', () => {
    const spokenAt80 = markSpoken(spokenNone, '1', 80);
    const result = decideVoice({
      maneuver: maneuver('1', 'Поверните'),
      distanceM: 80,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenAt80,
    });
    expect(result).toEqual({ type: 'silent' });
  });

  it('announces the next threshold even if a previous one was spoken', () => {
    const spokenAt80 = markSpoken(spokenNone, '1', 80);
    const result = decideVoice({
      maneuver: maneuver('1', 'Поверните'),
      distanceM: 30,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenAt80,
    });
    expect(result).toMatchObject({ type: 'announce', threshold: 30 });
  });

  it('announces at 80 even when the distance is larger', () => {
    // Distance is still within the 80 m band
    const result = decideVoice({
      maneuver: maneuver('1', 'Поверните'),
      distanceM: 70,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenNone,
    });
    expect(result).toMatchObject({ type: 'announce', threshold: 80 });
  });

  it('announces the smallest threshold the distance satisfies', () => {
    // Distance 15 m satisfies thresholds 0, 10, 30, and 80.
    // With ascending order [0, 10, 30, 80], 0 is checked first — distanceM=15 satisfies 0 (15 ≤ 0 is false),
    // then 10 (15 ≤ 10 is false), then 30 (15 ≤ 30 is true) — so 30 fires.
    const result = decideVoice({
      maneuver: maneuver('1', 'Поверните'),
      distanceM: 15,
      ...good(),
      offRoute: false,
      muted: false,
      spoken: spokenNone,
    });
    expect(result).toMatchObject({ type: 'announce', threshold: 30 });
  });
});

describe('decideTriggeredVoice', () => {
  it.each([400, 200, 50, 0])(
    'accepts the navigation engine %i m trigger',
    (distanceM) => {
      expect(
        decideTriggeredVoice({
          instruction: 'Поверните направо',
          distanceM,
          ...good(),
          offRoute: false,
          muted: false,
        })
      ).toEqual({
        type: 'announce',
        distanceM,
        instruction: 'Поверните направо',
      });
    }
  );

  it.each([
    { quality: 'poor' as const, offRoute: false, muted: false },
    { quality: 'good' as const, offRoute: true, muted: false },
    { quality: 'good' as const, offRoute: false, muted: true },
  ])('keeps the existing quality, off-route and mute gates', (gates) => {
    expect(
      decideTriggeredVoice({
        instruction: 'Поверните направо',
        distanceM: 200,
        ...gates,
      })
    ).toEqual({ type: 'silent' });
  });

  it('rejects invalid trigger distances', () => {
    expect(
      decideTriggeredVoice({
        instruction: 'Поверните направо',
        distanceM: Number.NaN,
        ...good(),
        offRoute: false,
        muted: false,
      })
    ).toEqual({ type: 'silent' });
  });
});

describe('isNewManeuver', () => {
  it('returns true when previous is null', () => {
    expect(isNewManeuver(null, maneuver('1'))).toBe(true);
  });

  it('returns true when next is null', () => {
    expect(isNewManeuver(maneuver('1'), null)).toBe(true);
  });

  it('returns true when keys differ', () => {
    expect(isNewManeuver(maneuver('1'), maneuver('2'))).toBe(true);
  });

  it('returns false when keys match', () => {
    expect(isNewManeuver(maneuver('1'), maneuver('1'))).toBe(false);
  });
});

describe('markSpoken', () => {
  it('adds the first threshold for a new maneuver', () => {
    const result = markSpoken(spokenNone, '1', 80);
    expect(result.get('1')?.has(80)).toBe(true);
  });

  it('preserves previously marked thresholds', () => {
    const with80 = markSpoken(spokenNone, '1', 80);
    const with30 = markSpoken(with80, '1', 30);
    expect(with30.get('1')?.has(80)).toBe(true);
    expect(with30.get('1')?.has(30)).toBe(true);
  });

  it('does not add a duplicate threshold', () => {
    const with80 = markSpoken(spokenNone, '1', 80);
    const again80 = markSpoken(with80, '1', 80);
    expect(again80.get('1')?.size).toBe(1);
  });

  it('keeps other maneuvers intact', () => {
    const with1 = markSpoken(spokenNone, '1', 80);
    const with2 = markSpoken(with1, '2', 30);
    expect(with2.get('1')?.has(80)).toBe(true);
    expect(with2.get('2')?.has(30)).toBe(true);
  });
});

beforeEach(() => {
  mockSpeechSynthesis.speak.mockClear();
  mockSpeechSynthesis.cancel.mockClear();
});

describe('speak', () => {
  it('cancels any in-progress speech before speaking', () => {
    speak('Поверните направо');
    expect(mockSpeechSynthesis.cancel).toHaveBeenCalled();
  });

  it('calls speechSynthesis.speak with the text', () => {
    speak('Поверните направо');
    expect(mockSpeechSynthesis.speak).toHaveBeenCalledOnce();
  });

  it('uses the ru-RU language by default', () => {
    speak('Поверните направо');
    const call = mockSpeechSynthesis.speak.mock.calls[0]![0] as {
      lang: string;
      text: string;
    };
    expect(call.lang).toBe('ru-RU');
  });

  it('uses the provided language', () => {
    speak('Turn right', 'en-US');
    const call = mockSpeechSynthesis.speak.mock.calls[0]![0] as {
      lang: string;
      text: string;
    };
    expect(call.lang).toBe('en-US');
  });

  it('constructs a real SpeechSynthesisUtterance, never a plain object', () => {
    speak('Поверните направо');
    expect(mockSpeechSynthesis.speak.mock.calls[0]![0]).toBeInstanceOf(
      MockSpeechSynthesisUtterance
    );
  });

  it('does not throw when SpeechSynthesisUtterance is missing', () => {
    const prev = window.SpeechSynthesisUtterance;
    // @ts-expect-error — deliberately absent in this test
    window.SpeechSynthesisUtterance = undefined;
    expect(() => speak('test')).not.toThrow();
    window.SpeechSynthesisUtterance = prev;
  });

  it('does not throw when speechSynthesis is undefined', () => {
    const prev = window.speechSynthesis;
    // @ts-expect-error — deliberately absent in this test
    window.speechSynthesis = undefined;
    expect(() => speak('test')).not.toThrow();
    window.speechSynthesis = prev;
  });
});

describe('cancelSpeech', () => {
  it('calls speechSynthesis.cancel', () => {
    cancelSpeech();
    expect(mockSpeechSynthesis.cancel).toHaveBeenCalledOnce();
  });

  it('does not throw when speechSynthesis is undefined', () => {
    const prev = window.speechSynthesis;
    // @ts-expect-error — deliberately absent in this test
    window.speechSynthesis = undefined;
    expect(() => cancelSpeech()).not.toThrow();
    window.speechSynthesis = prev;
  });
});

describe('isSpeechAvailable', () => {
  it('returns true when speechSynthesis is present', () => {
    expect(isSpeechAvailable()).toBe(true);
  });

  it('returns false when speechSynthesis is undefined', () => {
    const prev = window.speechSynthesis;
    // @ts-expect-error — deliberately absent in this test
    window.speechSynthesis = undefined;
    expect(isSpeechAvailable()).toBe(false);
    window.speechSynthesis = prev;
  });
});

describe('VOICE_THRESHOLDS_M', () => {
  it('is sorted ascending', () => {
    for (let i = 1; i < VOICE_THRESHOLDS_M.length; i++) {
      expect(VOICE_THRESHOLDS_M[i]!).toBeGreaterThan(
        VOICE_THRESHOLDS_M[i - 1]!
      );
    }
  });

  it('covers the pedestrian-relevant range 0–80', () => {
    expect(VOICE_THRESHOLDS_M[0]).toBe(0);
    expect(VOICE_THRESHOLDS_M[VOICE_THRESHOLDS_M.length - 1]).toBe(80);
  });
});
