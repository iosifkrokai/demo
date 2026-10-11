import type { MAP_STYLES } from './constants';

export type BuiltInMapStyleId = (typeof MAP_STYLES)[number]['id'];
export type MapStyleType = BuiltInMapStyleId | 'custom';
