export type BBox = [number, number, number, number];
export type Selection = {bbox: BBox; point?: {longitude: number; latitude: number}};
export type BoundsDraft = {west: string; south: string; east: string; north: string};

// Leaflet's default CRS (EPSG:3857) cannot render the geographic poles.  The
// legal interactive world therefore ends at the Web Mercator latitude limit.
export const WORLD_LONGITUDE_MIN = -180;
export const WORLD_LONGITUDE_MAX = 180;
export const MAP_LATITUDE_MIN = -85.0511287798066;
export const MAP_LATITUDE_MAX = 85.0511287798066;
export const WORLD_BOUNDS: BBox = [WORLD_LONGITUDE_MIN, MAP_LATITUDE_MIN, WORLD_LONGITUDE_MAX, MAP_LATITUDE_MAX];

export function toDraft(bbox: BBox): BoundsDraft {
  return {west: String(bbox[0]), south: String(bbox[1]), east: String(bbox[2]), north: String(bbox[3])};
}

export function parseBounds(draft: BoundsDraft): BBox {
  const strings = [draft.west, draft.south, draft.east, draft.north];
  if (strings.some(value => value.trim() === '')) throw new Error('请填写完整的四个区域边界。');
  const bounds = strings.map(Number) as BBox;
  if (bounds.some(value => !Number.isFinite(value))) throw new Error('经纬度必须是有效数字。');
  if (bounds[0] < WORLD_LONGITUDE_MIN || bounds[2] > WORLD_LONGITUDE_MAX || bounds[1] < MAP_LATITUDE_MIN || bounds[3] > MAP_LATITUDE_MAX) throw new Error('经度应在 -180～180；地图纬度应在 -85.051129～85.051129。');
  if (bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) throw new Error('西界必须小于东界，南界必须小于北界。');
  return bounds;
}

export function pointSelection(longitude: string, latitude: string, span: number, grid: number, coverage: BBox): Selection {
  if (!longitude.trim() || !latitude.trim()) throw new Error('请填写经度和纬度。');
  const lon = Number(longitude), lat = Number(latitude);
  if (!Number.isFinite(lon) || !Number.isFinite(lat) || lon < WORLD_LONGITUDE_MIN || lon > WORLD_LONGITUDE_MAX || lat < MAP_LATITUDE_MIN || lat > MAP_LATITUDE_MAX) throw new Error('经度应在 -180～180；地图纬度应在 -85.051129～85.051129。');
  let bbox: BBox;
  if (Math.abs(span - grid) < 1e-8) {
    // Snap to the containing original grid, including negative coordinates.
    const x = coverage[0] + Math.floor((lon - coverage[0]) / grid + 1e-9) * grid;
    const y = coverage[1] + Math.floor((lat - coverage[1]) / grid + 1e-9) * grid;
    bbox = [x, y, x + grid, y + grid];
  } else bbox = [lon - span / 2, lat - span / 2, lon + span / 2, lat + span / 2];
  bbox = [Math.max(WORLD_LONGITUDE_MIN, bbox[0]), Math.max(MAP_LATITUDE_MIN, bbox[1]), Math.min(WORLD_LONGITUDE_MAX, bbox[2]), Math.min(MAP_LATITUDE_MAX, bbox[3])].map(value => Number(value.toFixed(6))) as BBox;
  if (bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) throw new Error('该坐标位于边界，请选择有宽度的区域范围。');
  return {bbox, point: {longitude: lon, latitude: lat}};
}

export const sameBounds = (a: BBox, b: BBox) => a.every((value, index) => value === b[index]);
export const coordinateLabel = (value: number, axis: 'longitude' | 'latitude') => `${Math.abs(value).toFixed(3)}°${axis === 'longitude' ? value < 0 ? 'W' : 'E' : value < 0 ? 'S' : 'N'}`;
