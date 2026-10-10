import { describe, it, expect } from 'vitest';
import { buildHeightgraphData, colorMappings } from './heightgraph';

type Coordinate = [number, number];
type RangeHeightPoint = [number, number];

describe('buildHeightgraphData', () => {
  it('should handle empty input arrays', () => {
    const coordinates: Coordinate[] = [];
    const rangeHeightData: RangeHeightPoint[] = [];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result).toHaveLength(1);
    expect(result[0]!.type).toBe('FeatureCollection');
    expect(result[0]!.features).toHaveLength(0);
    expect(result[0]!.properties.summary).toBe('steepness');
    expect(result[0]!.properties.inclineTotal).toBe(0);
    expect(result[0]!.properties.declineTotal).toBe(0);
  });

  it('should handle single point data', () => {
    const coordinates: Coordinate[] = [[40.7128, -74.006]];
    const rangeHeightData: RangeHeightPoint[] = [[0, 10]];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result).toHaveLength(1);
    expect(result[0]!.features).toHaveLength(0);
    expect(result[0]!.properties.inclineTotal).toBe(0);
    expect(result[0]!.properties.declineTotal).toBe(0);
  });

  it('should calculate flat terrain (slope 0%)', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
      [40.713, -74.0062],
    ];
    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [100, 100],
      [200, 100],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result[0]!.features).toHaveLength(1);
    expect(result[0]!.features[0]!.properties.attributeType).toBe(0);
    expect(result[0]!.properties.inclineTotal).toBe(0);
    expect(result[0]!.properties.declineTotal).toBe(0);
  });

  it('should calculate steep uphill terrain (slope > 15%)', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
      [40.713, -74.0062],
    ];
    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [100, 120],
      [200, 140],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result[0]!.features).toHaveLength(1);
    expect(result[0]!.features[0]!.properties.attributeType).toBe(0);
    expect(result[0]!.properties.inclineTotal).toBe(40);
    expect(result[0]!.properties.declineTotal).toBe(0);
  });

  it('should calculate steep downhill terrain (slope < -15%)', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
      [40.713, -74.0062],
    ];
    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [100, 80],
      [200, 60],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result[0]!.features).toHaveLength(1);
    expect(result[0]!.features[0]!.properties.attributeType).toBe(0);
    expect(result[0]!.properties.inclineTotal).toBe(0);
    expect(result[0]!.properties.declineTotal).toBe(40);
  });

  it('should handle different slope ranges correctly', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
      [40.713, -74.0062],
      [40.7131, -74.0063],
      [40.7132, -74.0064],
      [40.7133, -74.0065],
    ];
    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [100, 102],
      [200, 105],
      [300, 107],
      [400, 111],
      [500, 116],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result[0]!.features.length).toBeGreaterThan(0);
    expect(result[0]!.properties.inclineTotal).toBe(16);
    expect(result[0]!.properties.declineTotal).toBe(0);
  });

  it('should create separate features for different height classes', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
      [40.713, -74.0062],
      [40.7131, -74.0063],
      [40.7132, -74.0064],
    ];
    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [100, 110],
      [200, 115],
      [300, 112],
      [400, 108],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result[0]!.features.length).toBeGreaterThan(1);

    const heightClasses = result[0]!.features.map(
      (f) => f.properties.attributeType
    );
    expect(new Set(heightClasses).size).toBeGreaterThan(1);

    expect(result[0]!.properties.inclineTotal).toBe(15);
    expect(result[0]!.properties.declineTotal).toBe(7);
  });

  it('should handle NaN slope values gracefully', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
    ];
    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [0, 105],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result[0]!.features).toHaveLength(1);
    expect(result[0]!.features[0]!.properties.attributeType).toBe(0);
  });

  it('should correctly format LineString coordinates', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
      [40.713, -74.0062],
    ];
    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [100, 102],
      [200, 104],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    if (result[0]!.features.length > 0) {
      const feature = result[0]!.features[0]!;
      expect(feature.type).toBe('Feature');
      expect(feature.geometry.type).toBe('LineString');
      expect(feature.geometry.coordinates.length).toBeGreaterThan(0);

      const firstCoord = feature.geometry.coordinates[0]!;
      expect(firstCoord).toHaveLength(4);
      expect(firstCoord[0]).toBe(40.7128);
      expect(firstCoord[1]).toBe(-74.006);
      expect(firstCoord[2]).toBe(100);
    }
  });

  it('should test all height class boundaries', () => {
    const coordinates: Coordinate[] = [
      [40.7128, -74.006],
      [40.7129, -74.0061],
      [40.713, -74.0062],
      [40.7131, -74.0063],
      [40.7132, -74.0064],
      [40.7133, -74.0065],
      [40.7134, -74.0066],
      [40.7135, -74.0067],
      [40.7136, -74.0068],
      [40.7137, -74.0069],
      [40.7138, -74.007],
    ];

    const rangeHeightData: RangeHeightPoint[] = [
      [0, 100],
      [100, 84],
      [200, 88],
      [300, 85.5],
      [400, 85.5],
      [500, 86.5],
      [600, 89.5],
      [700, 95.5],
      [800, 104.5],
      [900, 117.5],
      [1000, 133.5],
    ];

    const result = buildHeightgraphData(coordinates, rangeHeightData);

    expect(result[0]!.features.length).toBeGreaterThan(0);

    const heightClasses = result[0]!.features.map(
      (f) => f.properties.attributeType
    );

    expect(heightClasses).toContain(-5);
    expect(heightClasses).toContain(0);
    expect(heightClasses).toContain(1);
    expect(heightClasses).toContain(2);
  });
});

describe('colorMappings', () => {
  it('should have color mappings for all height classes', () => {
    expect(colorMappings.steepness).toBeDefined();

    for (let i = -5; i <= 5; i++) {
      const key = i.toString() as keyof typeof colorMappings.steepness;
      expect(colorMappings.steepness[key]).toBeDefined();
      expect(colorMappings.steepness[key].text).toBeDefined();
      expect(colorMappings.steepness[key].color).toBeDefined();
    }
  });

  it('should have valid color hex codes', () => {
    const hexColorRegex = /^#[0-9A-Fa-f]{6}$/;

    Object.values(colorMappings.steepness).forEach((mapping) => {
      expect(mapping.color).toMatch(hexColorRegex);
    });
  });

  it('should have descriptive text labels', () => {
    Object.values(colorMappings.steepness).forEach((mapping) => {
      expect(mapping.text).toBeTruthy();
      expect(typeof mapping.text).toBe('string');
      expect(mapping.text.length).toBeGreaterThan(0);
    });
  });
});
