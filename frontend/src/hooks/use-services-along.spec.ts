import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, renderHook, waitFor } from '@testing-library/react';

import type { ParsedDirectionsGeometry } from '@/components/types';
import { lineFromGeometry, useServicesAlong } from './use-services-along';

/** The two things this file exists to pin: */

const geometry = (points: number[][]): ParsedDirectionsGeometry =>
  ({ decodedGeometry: points }) as ParsedDirectionsGeometry;

describe('lineFromGeometry', () => {
  it('отдаёт GeoJSON-порядок координат, а не порядок Valhalla', () => {
    const line = lineFromGeometry(
      geometry([
        [53.68, 23.83],
        [53.67, 23.82],
      ])
    );
    expect(line).toEqual({
      type: 'LineString',
      coordinates: [
        [23.83, 53.68],
        [23.82, 53.67],
      ],
    });
  });

  it('одна точка — это не линия, измерять не по чему', () => {
    expect(lineFromGeometry(geometry([[53.68, 23.83]]))).toBeNull();
    expect(lineFromGeometry(geometry([]))).toBeNull();
    expect(lineFromGeometry(null)).toBeNull();
    expect(lineFromGeometry(undefined)).toBeNull();
  });
});

const answer = {
  items: [
    {
      id: 1,
      source_url: 'osm:node/1',
      name: 'Ссобойка',
      category: 'кафе',
      town: 'Гродно',
      lat: 53.679,
      lon: 23.825,
      opening_hours: 'Mo-Su 09:00-19:00',
      hours_known: true,
      off_line_m: 1,
      along_m: 417,
      along_fraction: 0.4,
      detour_confirmed: false,
    },
  ],
  measured: 'distance_to_line',
  not_measured: 'detour_walking_time',
  detour_confirmed: false,
  profile: 'pedestrian',
  categories: ['кафе', 'ресторан', 'туалет', 'гостиница'],
  max_off_line_m: 150,
  line_m: 1030,
  result_cap: 12,
  capped: false,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('useServicesAlong', () => {
  it('не спрашивает агента, пока подсказки не включены — и не притворяется', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() =>
      useServicesAlong(
        geometry([
          [53.68, 23.83],
          [53.67, 23.82],
        ]),
        {
          enabled: false,
        }
      )
    );

    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.state).toBe('idle');
    expect(result.current.items).toEqual([]);
  });

  it('отдаёт найденные точки и то, что именно было измерено', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => answer })
    );

    const { result } = renderHook(() =>
      useServicesAlong(
        geometry([
          [53.68, 23.83],
          [53.67, 23.82],
        ]),
        { enabled: true }
      )
    );

    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0]?.name).toBe('Ссобойка');
    expect(result.current.measured).toBe('distance_to_line');
    expect(result.current.maxOffLineM).toBe(150);
  });

  it('не зацикливается, когда родитель отдаёт новый объект геометрии', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => answer });
    vi.stubGlobal('fetch', fetchMock);

    const { result, rerender } = renderHook(
      ({ n }) =>
        useServicesAlong(
          {
            decodedGeometry: [
              [53.68, 23.83],
              [53.67, 23.82],
            ],
            n,
          } as unknown as ParsedDirectionsGeometry,
          { enabled: true }
        ),
      { initialProps: { n: 1 } }
    );

    await waitFor(() => expect(result.current.state).toBe('ready'));
    rerender({ n: 2 });
    rerender({ n: 3 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('падение — это «не удалось проверить», а не пустой список', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('boom')));

    const { result } = renderHook(() =>
      useServicesAlong(
        geometry([
          [53.68, 23.83],
          [53.67, 23.82],
        ]),
        { enabled: true }
      )
    );

    await waitFor(() => expect(result.current.state).toBe('unavailable'));
    expect(result.current.items).toEqual([]);
    expect(result.current.measured).toBeNull();
  });

  it('ошибку агента с кодом причины доводит наружу', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: { reason: 'shape_not_linestring' } }),
      })
    );

    const { result } = renderHook(() =>
      useServicesAlong(
        geometry([
          [53.68, 23.83],
          [53.67, 23.82],
        ]),
        { enabled: true }
      )
    );

    await waitFor(() => expect(result.current.state).toBe('unavailable'));
    expect(result.current.reason).toBe('shape_not_linestring');
  });
});
