type Coordinate = [number, number];
type RangeHeightPoint = [number, number];
type LineStringCoordinate = [number, number, number, number];
type HeightClass = -5 | -4 | -3 | -2 | -1 | 0 | 1 | 2 | 3 | 4 | 5;

interface HeightGraphFeature {
  type: 'Feature';
  geometry: {
    type: 'LineString';
    coordinates: LineStringCoordinate[];
  };
  properties: {
    attributeType: HeightClass;
  };
}

interface HeightGraphFeatureCollection {
  type: 'FeatureCollection';
  features: HeightGraphFeature[];
  properties: {
    summary: 'steepness';
    inclineTotal: number;
    declineTotal: number;
  };
}

const deriveHeightClass = (slope: number): HeightClass => {
  let heightClass: HeightClass;
  switch (true) {
    case slope < -15:
      heightClass = -5;
      break;
    case -15 <= slope && -10 > slope:
      heightClass = -4;
      break;
    case -10 <= slope && -7 > slope:
      heightClass = -3;
      break;
    case -7 <= slope && -4 > slope:
      heightClass = -2;
      break;
    case -4 <= slope && -1 > slope:
      heightClass = -1;
      break;
    case -1 <= slope && 1 > slope:
      heightClass = 0;
      break;
    case 1 <= slope && 3 > slope:
      heightClass = 1;
      break;
    case 3 <= slope && 6 > slope:
      heightClass = 2;
      break;
    case 6 <= slope && 9 > slope:
      heightClass = 3;
      break;
    case 9 <= slope && 15 > slope:
      heightClass = 4;
      break;
    case slope >= 15:
      heightClass = 5;
      break;
    default:
      heightClass = 0;
      break;
  }
  return heightClass;
};

export const buildHeightgraphData = (
  coordinates: Coordinate[],
  rangeHeightData: RangeHeightPoint[]
): HeightGraphFeatureCollection[] => {
  const features: HeightGraphFeature[] = [];

  let LineStringCoordinates: LineStringCoordinate[] = [];
  let previousHeightClass: HeightClass | undefined;

  let inclineTotal = 0;
  let declineTotal = 0;

  rangeHeightData.forEach((item, index) => {
    if (index < rangeHeightData.length - 1) {
      const riseThis = item[1];
      const riseNext = rangeHeightData[index + 1]![1];
      const rise = riseNext - riseThis;
      const run = rangeHeightData[index + 1]![0] - item[0];

      const slope = (rise / run) * 100;
      const heightClass: HeightClass = isNaN(slope)
        ? 0
        : deriveHeightClass(slope);

      if (rise > 0) {
        inclineTotal += rise;
      } else if (rise < 0) {
        declineTotal += rise * -1;
      }

      if (previousHeightClass !== heightClass) {
        LineStringCoordinates.push([
          coordinates[index]![0],
          coordinates[index]![1],
          rangeHeightData[index]![1],
          rangeHeightData[index]![0],
        ]);

        features.push({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: LineStringCoordinates,
          },
          properties: {
            attributeType: previousHeightClass || 0,
          },
        });
        LineStringCoordinates = [];
      }
      LineStringCoordinates.push([
        coordinates[index]![0],
        coordinates[index]![1],
        rangeHeightData[index]![1],
        rangeHeightData[index]![0],
      ]);
      previousHeightClass = heightClass;
    }
  });

  return [
    {
      type: 'FeatureCollection',
      features: features,
      properties: {
        summary: 'steepness',
        inclineTotal,
        declineTotal,
      },
    } as HeightGraphFeatureCollection,
  ];
};

export const colorMappings = {
  steepness: {
    '-5': {
      text: '16%+',
      color: '#028306',
    },
    '-4': {
      text: '10-15%',
      color: '#2AA12E',
    },
    '-3': {
      text: '7-9%',
      color: '#53BF56',
    },
    '-2': {
      text: '4-6%',
      color: '#7BDD7E',
    },
    '-1': {
      text: '1-3%',
      color: '#A4FBA6',
    },
    0: {
      text: '0%',
      color: '#ffcc99',
    },
    1: {
      text: '1-3%',
      color: '#F29898',
    },
    2: {
      text: '4-6%',
      color: '#E07575',
    },
    3: {
      text: '7-9%',
      color: '#CF5352',
    },
    4: {
      text: '10-15%',
      color: '#BE312F',
    },
    5: {
      text: '16%+',
      color: '#AD0F0C',
    },
  },
};
