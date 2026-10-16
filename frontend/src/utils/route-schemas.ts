import { z } from 'zod';
import { fallback } from '@tanstack/zod-adapter';
import { profileEnum } from '../stores/common-store';
import { mapStyleSchema } from '../components/map/utils';

export const searchParamsSchema = z.object({
  profile: fallback(profileEnum.optional(), 'pedestrian'),
  at: z.string().optional(),
  wps: z.string().optional(),
  range: z.number().optional(),
  interval: z.number().optional(),
  generalize: z.number().optional(),
  denoise: z.number().optional(),
  style: mapStyleSchema.optional(),
});

export const VALID_TABS = ['directions'] as const;
export type ValidTab = (typeof VALID_TABS)[number];

export function isValidTab(tab: string): tab is ValidTab {
  return VALID_TABS.includes(tab as ValidTab);
}
