import L from 'leaflet';

export type RasterCell = {cell_id: string; month: string; cell_ll_lon: number; cell_ll_lat: number; [field: string]: number | string | boolean | null};
export type ColorRange = {min: number; max: number};
export const environmentPalette = ['#2c6682', '#459a9b', '#81c4a5', '#d4dc9b', '#f3bf75', '#dc875f'];
export const effortPalette = ['#c4a5f7', '#ac7aeb', '#9354d7', '#7936be', '#60229e', '#431277'];
export const effortZeroColor = '#94a3b8';
/** Diverging blue-grey-warm ramp; the middle class is the zero-difference band. */
export const deltaSstPalette = ['#2c5f9e', '#5a8dc0', '#a8c4dc', '#eceff1', '#f2c39b', '#e08a5f', '#c25236'];

/** A no-data cell must never be painted as a near-zero difference. */
export function deltaSstColor(value: number | null | undefined, range: ColorRange) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return rasterColor(value, range, deltaSstPalette, false);
}

/** Logarithmic symbol size; zoom changes spacing, not the time reference. */
export function effortSymbolRadius(value: number, max: number, cellPixels: number) {
  const fraction = max > 0 ? Math.log1p(Math.max(0, value)) / Math.log1p(max) : 0;
  const largest = Math.max(3.5, Math.min(10, cellPixels * .42));
  return 1.45 + (largest - 1.45) * Math.sqrt(Math.max(0, Math.min(1, fraction)));
}

export function rasterColor(value: number, range: ColorRange, palette: string[], logarithmic = false) {
  const fraction = logarithmic ? (range.max > 0 ? Math.log1p(Math.max(0, value)) / Math.log1p(range.max) : 0)
    : range.max === range.min ? .5 : (value - range.min) / (range.max - range.min);
  const position = Math.max(0, Math.min(1, fraction)) * (palette.length - 1);
  const index = Math.min(palette.length - 2, Math.floor(position));
  const weight = position - index;
  const rgb = (hex: string) => [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16));
  const a = rgb(palette[index]), b = rgb(palette[index + 1]);
  return `rgb(${a.map((channel, i) => Math.round(channel + (b[i] - channel) * weight)).join(',')})`;
}

type RasterOptions = {pane: string; field: string; month: string; grid: number; range: ColorRange; opacity: number; logarithmic?: boolean; showGrid: boolean; effortMode?: 'symbols' | 'raster'; showZero?: boolean; palette?: string[]; outlinesOnly?: boolean; markField?: string};

/** A single geographic canvas per layer, not one DOM path per cell. */
export class RasterLayer extends L.Layer {
  private activeMap: L.Map | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private scheduled: number | null = null;
  private records: {west: number; south: number; color: string; value: number; marked: boolean}[];
  constructor(cells: RasterCell[], private config: RasterOptions) {
    super();
    const palette = config.palette ?? (config.logarithmic ? effortPalette : environmentPalette);
    this.records = cells.flatMap(cell => {
      const value = cell[config.field];
      return typeof value === 'number' && Number.isFinite(value) ? [{west: cell.cell_ll_lon, south: cell.cell_ll_lat, value, color: config.logarithmic && value === 0 ? effortZeroColor : rasterColor(value, config.range, palette, config.logarithmic), marked: config.markField ? cell[config.markField] === true || cell[config.markField] === 1 : false}] : [];
    });
    if (config.effortMode === 'symbols') this.records.sort((a, b) => a.value - b.value);
  }
  override onAdd(map: L.Map): this {
    this.activeMap = map;
    this.canvas = L.DomUtil.create('canvas', 'sample-raster', map.getPane(this.config.pane));
    this.canvas.style.opacity = String(this.config.opacity);
    this.canvas.setAttribute('aria-label', this.config.pane === 'environment' ? '环境要素栅格' : this.config.effortMode === 'symbols' ? '捕捞努力量比例符号' : '捕捞努力量栅格');
    this.canvas.dataset.field = this.config.field;
    this.canvas.dataset.month = this.config.month;
    this.canvas.dataset.cells = String(this.records.length);
    this.canvas.dataset.grid = String(this.config.grid);
    this.canvas.dataset.mode = this.config.effortMode ?? 'raster';
    this.canvas.dataset.positiveCells = String(this.records.filter(record => record.value > 0).length);
    this.canvas.dataset.zeroCells = String(this.records.filter(record => record.value === 0).length);
    this.canvas.dataset.showZero = String(this.config.showZero ?? true);
    this.canvas.dataset.markedCells = String(this.records.filter(record => record.marked).length);
    map.on('move zoom resize', this.queueDraw, this);
    this.queueDraw();
    return this;
  }
  override onRemove(map: L.Map): this {
    map.off('move zoom resize', this.queueDraw, this);
    if (this.scheduled !== null) cancelAnimationFrame(this.scheduled);
    this.scheduled = null;
    this.canvas?.remove(); this.canvas = null; this.activeMap = null;
    return this;
  }
  private queueDraw = () => {
    if (this.scheduled !== null) return;
    this.scheduled = requestAnimationFrame(() => {this.scheduled = null; this.draw();});
  };
  private draw() {
    const map = this.activeMap, canvas = this.canvas;
    if (!map || !canvas) return;
    const started = performance.now();
    const size = map.getSize(), ratio = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(size.x * ratio) || canvas.height !== Math.round(size.y * ratio)) {
      canvas.width = Math.round(size.x * ratio); canvas.height = Math.round(size.y * ratio);
    }
    canvas.style.width = `${size.x}px`; canvas.style.height = `${size.y}px`;
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    const context = canvas.getContext('2d')!;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size.x, size.y);
    const xs = new Map<string, number>(), ys = new Map<string, number>();
    const x = (longitude: number) => {
      const key = longitude.toFixed(8);
      if (!xs.has(key)) xs.set(key, Math.round(map.latLngToContainerPoint([0, Number(key)]).x));
      return xs.get(key)!;
    };
    const y = (latitude: number) => {
      const key = latitude.toFixed(8);
      if (!ys.has(key)) ys.set(key, Math.round(map.latLngToContainerPoint([Number(key), 0]).y));
      return ys.get(key)!;
    };
    let visible = 0;
    for (const record of this.records) {
      if (this.config.logarithmic && record.value === 0 && this.config.showZero === false) continue;
      const left = x(record.west), right = x(record.west + this.config.grid);
      const top = y(record.south + this.config.grid), bottom = y(record.south);
      if (right < 0 || left > size.x || bottom < 0 || top > size.y) continue;
      if (this.config.outlinesOnly) {
        context.strokeStyle = '#223f46'; context.lineWidth = .85;
        context.strokeRect(left+.5, top+.5, Math.max(0,right-left-1), Math.max(0,bottom-top-1));
      } else if (this.config.effortMode === 'symbols') {
        const cx = x(record.west + this.config.grid / 2), cy = y(record.south + this.config.grid / 2);
        if (record.value === 0) {
          // A small cross is a known zero, never a positive-activity circle.
          context.beginPath(); context.moveTo(cx - 1.7, cy - 1.7); context.lineTo(cx + 1.7, cy + 1.7);
          context.moveTo(cx - 1.7, cy + 1.7); context.lineTo(cx + 1.7, cy - 1.7);
          context.strokeStyle = '#ffffff'; context.lineWidth = 2.7; context.stroke();
          context.strokeStyle = '#64748b'; context.lineWidth = 1; context.stroke();
        } else {
          const radius = effortSymbolRadius(record.value, this.config.range.max, Math.min(right-left, bottom-top));
          context.beginPath(); context.arc(cx, cy, radius, 0, Math.PI * 2);
          context.strokeStyle = '#ffffff'; context.lineWidth = 3.4; context.stroke();
          context.fillStyle = '#7c3aed'; context.fill();
          context.strokeStyle = '#3b136b'; context.lineWidth = 1.5; context.stroke();
        }
      } else {
        context.fillStyle = record.color;
        context.fillRect(left, top, right - left, bottom - top);
        if (record.marked && right - left >= 4 && bottom - top >= 4) {
          // The candidate outline marks the |difference| threshold; it never recolours the cell.
          context.strokeStyle = '#223f46'; context.lineWidth = .9;
          context.strokeRect(left+.45, top+.45, Math.max(0,right-left-.9), Math.max(0,bottom-top-.9));
        } else if (this.config.logarithmic && record.value > 0) {
          context.strokeStyle = 'rgba(62,18,112,.65)'; context.lineWidth = .55;
          context.strokeRect(left+.25, top+.25, Math.max(0,right-left-.5), Math.max(0,bottom-top-.5));
        } else if (this.config.showGrid && right - left >= 5 && bottom - top >= 5) {
          context.strokeStyle = 'rgba(255,255,255,.36)'; context.lineWidth = .5;
          context.strokeRect(left, top, right - left, bottom - top);
        }
      }
      visible++;
    }
    canvas.dataset.visibleCells = String(visible);
    canvas.dataset.paintMs = (performance.now() - started).toFixed(3);
  }
}
