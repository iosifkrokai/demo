import type { NominationResponse } from '@/components/types';

export const NOMINATIM_URL = `${import.meta.env.VITE_NOMINATIM_URL}/search`;

export const forward_geocode = async (userInput: string) => {
  const params = new URLSearchParams({
    q: userInput,
    format: 'json',
    limit: '5',
  });
  const response = await fetch(`${NOMINATIM_URL}?${params}`);

  if (!response.ok) {
    throw new Error(`Could not fetch resource`);
  }
  const data: NominationResponse[] = await response.json();
  return { data };
};
