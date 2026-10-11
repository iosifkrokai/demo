/** The editable fields of a place, as the admin panel edits them. */

import type { AdminPlaceInput, Place } from '@/api/types';

export interface EditableFields {
  name: string;
  category: string;
  town: string;
  visitMinutes: string;
  blurb: string;
  funFact: string;
  lat: string;
  lon: string;
}

/** ~1 cm — enough for a street address, short enough to read in an input. */
export const COORD_PRECISION = 7;

export const fieldsOf = (place: Place): EditableFields => ({
  name: place.name,
  category: place.category ?? '',
  town: place.town ?? '',
  visitMinutes: place.visit_minutes == null ? '' : String(place.visit_minutes),
  blurb: place.blurb ?? '',
  funFact: place.fun_fact ?? '',
  lat: String(place.lat),
  lon: String(place.lon),
});

/** `''` or garbage → `null` («leave unchanged»), never `NaN`/`0`. */
const num = (value: string): number | null => {
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

export const toPlacePatch = (fields: EditableFields): AdminPlaceInput => {
  const patch: AdminPlaceInput = {
    name: fields.name.trim(),
    category: fields.category.trim() || null,
    town: fields.town.trim() || null,
    fun_fact: fields.funFact.trim() || null,
    blurb: fields.blurb.trim() || null,
    visit_minutes: num(fields.visitMinutes),
  };

  const lat = num(fields.lat);
  const lon = num(fields.lon);
  if (lat !== null) patch.lat = lat;
  if (lon !== null) patch.lon = lon;

  return patch;
};

/** A dragged coordinate as input text, without float noise. */
export const formatCoord = (value: number): string =>
  String(Number(value.toFixed(COORD_PRECISION)));
