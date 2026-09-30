import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { PossibleSettings } from '@/components/types';
import {
  settingsInit,
  settingsInitTruckOverride,
  QUICK_SETTING_PARAMS,
} from '@/components/settings-panel/settings-options';
import { z } from 'zod';

export const profileEnum = z.enum([
  'auto',
  'bicycle',
  'pedestrian',
  'car',
  'truck',
  'bus',
  'motor_scooter',
  'motorcycle',
]);

export type Profile = z.infer<typeof profileEnum>;

interface CommonState {
  settingsPanelOpen: boolean;
  directionsPanelOpen: boolean;
  coordinates: number[][];
  loading: boolean;
  settings: PossibleSettings;
  dateTime: { type: number; value: string };
  mapReady: boolean;
  /**
   * A one-shot «look here» request from a panel row to the map.
   *
   * `at` is a timestamp rather than a flag so tapping the same place twice
   * still moves the map: the payload changes even when the coordinates do not.
   */
  focus: { lng: number; lat: number; at: number } | null;
  /** The guide's latest fix, published so the map can follow it. */
  guideFix: {
    lng: number;
    lat: number;
    /** Compass heading in degrees, when the device reports one. */
    heading: number | null;
    at: number;
  } | null;
  /** True while the guide (Проводник) is running. */
  guiding: boolean;
  /**
   * Metres to the next turn, published by the guide so the camera can behave
   * like a navigator instead of a viewer: it closes in as the turn comes.
   * null while there is no line, no trusted fix, or nothing to turn into.
   */
  guideTurnDistanceM: number | null;
}

interface CommonActions {
  showLoading: (loading: boolean) => void;
  zoomTo: (coordinates: number[][]) => void;
  toggleSettings: () => void;
  toggleDirections: () => void;
  updateSettings: (
    name: keyof PossibleSettings,
    value: PossibleSettings[keyof PossibleSettings]
  ) => void;
  resetSettings: (profile: Profile) => void;
  updateDateTime: (key: 'type' | 'value', value: string | number) => void;
  setMapReady: (ready: boolean) => void;
  /** Ask the map to look at one spot. */
  focusOn: (lng: number, lat: number) => void;
  setGuideFix: (fix: CommonState['guideFix']) => void;
  setGuiding: (guiding: boolean) => void;
  setGuideTurnDistanceM: (metres: number | null) => void;
}

type CommonStore = CommonState & CommonActions;

// Open the left panel by default on non-mobile viewports (Tailwind md breakpoint).
const DEFAULT_PANEL_OPEN =
  typeof window !== 'undefined' && window.innerWidth >= 768;

export const useCommonStore = create<CommonStore>()(
  devtools(
    immer((set) => ({
      settingsPanelOpen: false,
      directionsPanelOpen: DEFAULT_PANEL_OPEN,
      coordinates: [],
      loading: false,
      settings: { ...settingsInit },
      dateTime: {
        type: -1,
        value: new Date().toISOString().slice(0, 16),
      },
      mapReady: false,
      focus: null,
      guideFix: null,
      guiding: false,
      guideTurnDistanceM: null,

      focusOn: (lng, lat) =>
        set({ focus: { lng, lat, at: Date.now() } }, undefined, 'focusOn'),
      setGuideFix: (fix) => set({ guideFix: fix }, undefined, 'setGuideFix'),
      setGuiding: (guiding) => set({ guiding }, undefined, 'setGuiding'),
      setGuideTurnDistanceM: (metres) =>
        set({ guideTurnDistanceM: metres }, undefined, 'setGuideTurnDistanceM'),

      showLoading: (loading) => set({ loading }),
      zoomTo: (coordinates) => set({ coordinates }),
      setMapReady: (ready) => set({ mapReady: ready }),
      toggleSettings: () =>
        set(
          (state) => {
            state.settingsPanelOpen = !state.settingsPanelOpen;
          },
          undefined,
          'toggleSettings'
        ),
      toggleDirections: () =>
        set(
          (state) => {
            state.directionsPanelOpen = !state.directionsPanelOpen;
          },
          undefined,
          'toggleDirections'
        ),
      updateSettings: (name, value) =>
        set(
          (state) => {
            state.settings[name] = value;
          },
          undefined,
          'updateSettings'
        ),
      resetSettings: (profile) =>
        set(
          (state) => {
            const base =
              profile === 'truck' ? settingsInitTruckOverride : settingsInit;
            // Preserve quick-panel params — they're treated as cross-profile
            // user preferences (URL-permalinked) and shouldn't be wiped on a
            // profile change or an explicit Reset.
            const preserved: Partial<PossibleSettings> = {};
            for (const param of QUICK_SETTING_PARAMS) {
              preserved[param] = state.settings[param];
            }
            state.settings = { ...base, ...preserved };
          },
          undefined,
          'resetSettings'
        ),
      updateDateTime: (key, value) =>
        set(
          (state) => {
            // The Select control fires onValueChange with strings; coerce here
            // so type stays numeric (formatTriggerLabel uses === comparisons).
            if (key === 'type') state.dateTime.type = Number(value);
            else state.dateTime.value = value as string;
          },
          undefined,
          'updateDateTime'
        ),
    })),
    { name: 'common' }
  )
);
