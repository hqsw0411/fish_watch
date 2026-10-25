import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import L from 'leaflet';
import type { GeoJsonObject } from 'geojson';
import { interpretCommand } from './commands';
import { coordinateLabel, MAP_LATITUDE_MAX, MAP_LATITUDE_MIN, WORLD_BOUNDS, parseBounds, pointSelection, sameBounds, toDraft, type BBox, type BoundsDraft, type Selection } from './spatial';
import { RasterLayer, deltaSstColor, deltaSstPalette, effortSymbolRadius, type RasterCell } from './raster';
import 'leaflet/dist/leaflet.css';
import './style.css';

type Cell = { month: string; cell_id: string; cell_ll_lon: number; cell_ll_lat: number; longitude: number; latitude: number; fishing_hours: number | null; effort_status: string; [key: string]: number | string | null };
type Catalog = { name: string; bbox: BBox; initial_bbox: BBox; overview_bbox?: BBox | null; regions?: {id: string; name: string; bbox: BBox}[]; fronts?: {available: boolean; reason: string}; max_region_cells: number; months: string[]; rows: number; grid_degrees: number; cells_per_month: number; environment_fields: string[]; scales: Record<string, {min: number; max: number}> };
type Sample = { month: string; grid_degrees: number; requested_bbox: BBox; effective_bbox: BBox | null; coverage_status: 'full' | 'partial' | 'outside'; cells: Cell[]; summary: { cells: number; observed_cells: number; fishing_hours: number | null; environment_valid_cells: Record<string, number> } };
/** SST difference plus the fixed-domain reference and the sample-internal descriptive checks. */
type Temperature = { month: string; baseline: 'regional' | 'previous'; percentile: number; grid_degrees: number; requested_bbox: BBox; effective_bbox: BBox | null; coverage_status: 'full' | 'partial' | 'outside'; cells: Cell[]; reference: {regional_mean_sst_c: number | null; valid_sst_cells: number; valid_delta_cells: number; previous_month: string | null; label: string; threshold_abs_delta_c: number | null}; scale: {min: number; max: number}; summary: {cells: number; valid_delta_cells: number; candidate_cells: number; paired_cells: number; candidate_paired_cells: number; known_zero_cells: number; missing_effort_cells: number; paired_fishing_hours: number | null; candidate_fishing_hours: number | null; candidate_known_cell_share: number | null; candidate_effort_share: number | null; effort_concentration_ratio: number | null; pearson_delta_log_effort: number | null; pearson_abs_delta_log_effort: number | null}; interpretation: string };
const factors = [
  {field: 'sst_c', name: '海表温度 SST', unit: '°C', short: '表层海温'},
  {field: 'chl_mg_m3', name: '叶绿素 Chl-a', unit: 'mg/m³', short: '叶绿素'},
  {field: 'npp_mg_c_m3_day', name: '初级生产力 NPP', unit: 'mg C/m³/day', short: '体积初级生产力'},
  {field: 'ssh_m', name: '海面高度 SSH', unit: 'm', short: '海面高度'},
  {field: 'ssha_m', name: '海面高度距平 SSHA', unit: 'm', short: '海面高度距平'},
];
const number = (n: number | null | undefined, decimals = 1) => n == null ? '—' : n.toLocaleString('zh-CN', {maximumFractionDigits: decimals});
async function api<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {signal});
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `数据读取失败（${response.status}）`);
  }
  return response.json();
}

function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [sampleResponse, setSample] = useState<Sample | null>(null);
  const [month, setMonth] = useState('');
  const [factor, setFactor] = useState('sst_c');
  const [envVisible, setEnvVisible] = useState(true);
  const [effortVisible, setEffortVisible] = useState(true);
  const [effortOpacity, setEffortOpacity] = useState(1);
  const [effortStyle, setEffortStyle] = useState<'auto' | 'symbols' | 'raster'>('auto');
  const [showZeroEffort, setShowZeroEffort] = useState(false);
  const [symbolCellPixels, setSymbolCellPixels] = useState(6);
  const [gridVisible, setGridVisible] = useState(true);
  const [deltaVisible, setDeltaVisible] = useState(false);
  const [deltaBaseline, setDeltaBaseline] = useState<'regional' | 'previous'>('regional');
  const [deltaPercentile, setDeltaPercentile] = useState(75);
  const [temperatureResponse, setTemperatureResponse] = useState<Temperature | null>(null);
  const [regionChoice, setRegionChoice] = useState('all');
  const [hoveredCell, setHoveredCell] = useState<Cell | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [landState, setLandState] = useState('loading');
  const [cursor, setCursor] = useState('将鼠标移至地图查看坐标');
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<{role: 'user' | 'assistant'; text: string}[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [selectionMode, setSelectionMode] = useState<'point' | 'area'>('area');
  const [boundsDraft, setBoundsDraft] = useState<BoundsDraft>({west: '-62', south: '-48', east: '-60', north: '-46'});
  const [longitude, setLongitude] = useState('-61');
  const [latitude, setLatitude] = useState('-47');
  const [pointSpan, setPointSpan] = useState(.1);
  const [pickMode, setPickMode] = useState<'point' | 'area' | null>(null);
  const [spatialError, setSpatialError] = useState('');
  const chatHistory = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const envGroup = useRef<L.LayerGroup | null>(null);
  const effortGroup = useRef<L.LayerGroup | null>(null);
  const hoverIndex = useRef<{grid: number; origin: BBox; cells: Map<string, Cell>} | null>(null);
  const sample = sampleResponse && selection && sampleResponse.month === month && sameBounds(sampleResponse.requested_bbox, selection.bbox) ? sampleResponse : null;
  const effortMode = effortStyle === 'auto' ? envVisible ? 'symbols' : 'raster' : effortStyle;
  // A difference layer is only drawn for its own month and selection; no stale raster is kept.
  const temperature = deltaVisible && temperatureResponse && selection && temperatureResponse.month === month && sameBounds(temperatureResponse.requested_bbox, selection.bbox) ? temperatureResponse : null;
  const selectedDeltaRange = temperature ? {min: temperature.scale.min, max: temperature.scale.max} : null;

  function applySelection(next: Selection, fit = true) {
    setSelection(next); setBoundsDraft(toDraft(next.bbox)); setSpatialError(''); setPickMode(null);
    setLoading(true); setError(''); setSample(null);
    if (catalog) setRegionChoice(sameBounds(next.bbox, catalog.initial_bbox) ? 'all' : catalog.regions?.find(region => sameBounds(region.bbox, next.bbox))?.id ?? 'custom');
    if (next.point) {setLongitude(String(next.point.longitude)); setLatitude(String(next.point.latitude));}
    if (fit) {
      const [w, s, e, n] = next.bbox;
      map.current?.fitBounds([[s, w], [n, e]], {padding: [38, 38], maxZoom: next.point ? 10 : 9, animate: false});
    }
  }

  function applyCoordinates() {
    if (!catalog) return;
    try {applySelection(pointSelection(longitude, latitude, pointSpan, catalog.grid_degrees, catalog.bbox));}
    catch (error) {setSpatialError((error as Error).message);}
  }

  function resetSelection() {
    if (catalog) applySelection({bbox: catalog.initial_bbox});
  }

  function returnToSelection() {
    if (!selection) return;
    const [w, s, e, n] = selection.bbox;
    map.current?.fitBounds([[s, w], [n, e]], {padding: [38, 38], maxZoom: selection.point ? 10 : 9, animate: false});
  }

  function clampMapLatLng(latlng: L.LatLng) {
    return L.latLng(
      Math.max(MAP_LATITUDE_MIN, Math.min(MAP_LATITUDE_MAX, latlng.lat)),
      Math.max(WORLD_BOUNDS[0], Math.min(WORLD_BOUNDS[2], latlng.lng)),
    );
  }

  useEffect(() => { const history = chatHistory.current; if (history) history.scrollTop = history.scrollHeight; }, [messages]);
  useEffect(() => {
    const history = chatHistory.current;
    if (!history) return;
    const observer = new ResizeObserver(() => { history.scrollTop = history.scrollHeight; });
    observer.observe(history);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api<Catalog>('/api/catalog', controller.signal).then(data => {
      setCatalog(data); setMonth(data.months[0]);
      setSelection({bbox: data.initial_bbox}); setBoundsDraft(toDraft(data.initial_bbox));
      setLongitude(String((data.initial_bbox[0] + data.initial_bbox[2]) / 2));
      setLatitude(String((data.initial_bbox[1] + data.initial_bbox[3]) / 2));
      setPointSpan(data.grid_degrees);
    }).catch(e => { if (e.name !== 'AbortError') {setError(e.message); setLoading(false);} });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!month || !selection) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setSample(null);
    const [west, south, east, north] = selection.bbox;
    const params = new URLSearchParams({month, west: String(west), south: String(south), east: String(east), north: String(north)});
    api<Sample>(`/api/sample?${params}`, controller.signal).then(data => {
      if (!controller.signal.aborted) {setSample(data); setLoading(false);}
    }).catch(e => { if (e.name !== 'AbortError') {setError(e.message); setLoading(false);} });
    return () => controller.abort();
  }, [month, selection]);

  useEffect(() => {
    if (!month || !selection || !deltaVisible) return;
    const controller = new AbortController();
    const [west, south, east, north] = selection.bbox;
    const params = new URLSearchParams({month, baseline: deltaBaseline, percentile: String(deltaPercentile), west: String(west), south: String(south), east: String(east), north: String(north)});
    setTemperatureResponse(null);
    api<Temperature>(`/api/temperature?${params}`, controller.signal).then(data => {
      if (!controller.signal.aborted) setTemperatureResponse(data);
    }).catch(e => {
      if (e.name === 'AbortError') return;
      setTemperatureResponse(null);
      setMessages(previous => [...previous, {role: 'assistant', text: `海表温差图层未能计算：${e.message}`}]);
    });
    return () => controller.abort();
  }, [month, selection, deltaVisible, deltaBaseline, deltaPercentile]);

  useEffect(() => {
    if (!container.current) return;
    const instance = L.map(container.current, {zoomControl: false, minZoom: 3, maxZoom: 11, maxBounds: L.latLngBounds([[WORLD_BOUNDS[1], WORLD_BOUNDS[0]], [WORLD_BOUNDS[3], WORLD_BOUNDS[2]]]), maxBoundsViscosity: 1, worldCopyJump: false, attributionControl: false, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false});
    map.current = instance;
    container.current.dataset.worldBounds = WORLD_BOUNDS.join(',');
    instance.fitBounds([[-60, -180], [60, 180]]);
    L.control.zoom({position: 'topright'}).addTo(instance);
    L.control.scale({position: 'bottomleft', imperial: false}).addTo(instance);
    L.control.attribution({position: 'bottomright', prefix: false}).addAttribution('Natural Earth · 公共领域 | GFW · Copernicus Marine').addTo(instance);
    instance.createPane('environment'); instance.getPane('environment')!.style.zIndex = '410';
    instance.createPane('effort'); instance.getPane('effort')!.style.zIndex = '420';
    instance.createPane('selection'); instance.getPane('selection')!.style.zIndex = '440';
    const updateSymbolScale = () => setSymbolCellPixels(Math.abs(instance.latLngToContainerPoint([0, .1]).x - instance.latLngToContainerPoint([0, 0]).x));
    instance.on('zoomend resize', updateSymbolScale); updateSymbolScale();
    envGroup.current = L.layerGroup().addTo(instance);
    effortGroup.current = L.layerGroup().addTo(instance);
    const base = L.layerGroup().addTo(instance);
    const controller = new AbortController();
    api<GeoJsonObject>('/land.geojson', controller.signal).then(land => {
      L.geoJSON(land, {style: {color: '#bdc8c5', weight: 1.1, fillColor: '#e6e9df', fillOpacity: 1}, interactive: false}).addTo(base);
      setLandState('ready');
    }).catch(e => {if (e.name !== 'AbortError') setLandState('error');});
    instance.on('mousemove', e => {
      const bounded = clampMapLatLng(e.latlng);
      setCursor(`${Math.abs(bounded.lng).toFixed(3)}°${bounded.lng < 0 ? 'W' : 'E'}  /  ${Math.abs(bounded.lat).toFixed(3)}°${bounded.lat < 0 ? 'S' : 'N'}`);
      const index = hoverIndex.current;
      const x = index ? index.origin[0] + Math.floor((bounded.lng - index.origin[0]) / index.grid + 1e-9) * index.grid : 0;
      const y = index ? index.origin[1] + Math.floor((bounded.lat - index.origin[1]) / index.grid + 1e-9) * index.grid : 0;
      const cell = index?.cells.get(`${x.toFixed(4)}_${y.toFixed(4)}`) ?? null;
      setHoveredCell(previous => previous?.cell_id === cell?.cell_id && previous?.month === cell?.month ? previous : cell);
    });
    instance.on('mouseout', () => setHoveredCell(null));
    const resize = new ResizeObserver(() => instance.invalidateSize()); resize.observe(container.current);
    return () => {controller.abort(); resize.disconnect(); instance.remove(); map.current = null;};
  }, []);

  useEffect(() => {
    if (!map.current || !catalog) return;
    const [w, s, e, n] = catalog.bbox;
    const border = L.rectangle([[s, w], [n, e]], {color: '#6b8490', dashArray: '6 5', weight: 1, fill: false, interactive: false}).addTo(map.current);
    const [iw, is, ie, inn] = catalog.overview_bbox ?? [catalog.initial_bbox[0] - 7, catalog.initial_bbox[1] - 2.5, catalog.initial_bbox[2] + 5.5, catalog.initial_bbox[3] + 3];
    map.current.fitBounds([[Math.max(-85, is), Math.max(-180, iw)], [Math.min(85, inn), Math.min(180, ie)]], {animate: false});
    const guides = L.layerGroup().addTo(map.current);
    for (let lat = Math.ceil(is / 2) * 2; lat <= inn; lat += 2) {
      L.polyline([[Math.max(-85, lat), Math.max(-180, iw)], [Math.min(85, lat), Math.min(180, ie)]], {color: '#a7bbc6', weight: .5, opacity: .35, interactive: false}).addTo(guides);
    }
    for (let lon = Math.ceil(iw / 2) * 2; lon <= ie; lon += 2) {
      L.polyline([[Math.max(-85, is), Math.max(-180, lon)], [Math.min(85, inn), Math.min(180, lon)]], {color: '#a7bbc6', weight: .5, opacity: .35, interactive: false}).addTo(guides);
    }
    const blocks = L.layerGroup().addTo(map.current);
    catalog.regions?.forEach((region, i) => {
      const [rw, rs, re, rn] = region.bbox;
      L.rectangle([[rs, rw], [rn, re]], {pane: 'selection', color: '#557d8c', weight: 1, opacity: .6, dashArray: '4 4', fill: false, interactive: false}).addTo(blocks);
      L.marker([rn - .18, rw + .18], {pane: 'selection', interactive: false, icon: L.divIcon({className: 'block-label', html: String(i + 1), iconSize: [19, 19]})}).addTo(blocks);
    });
    return () => {border.remove(); guides.remove(); blocks.remove();};
  }, [catalog]);

  useEffect(() => {
    if (!map.current || !selection) return;
    const [w, s, e, n] = selection.bbox;
    const outline = L.rectangle([[s, w], [n, e]], {pane: 'selection', className: 'selected-bounds', color: '#13899b', weight: 2, fill: false, interactive: false}).addTo(map.current);
    const marker = selection.point ? L.circleMarker([selection.point.latitude, selection.point.longitude], {pane: 'selection', className: 'selected-point', radius: 6, color: '#fff', weight: 2, fillColor: '#126b9b', fillOpacity: 1, interactive: false}).addTo(map.current) : null;
    return () => {outline.remove(); marker?.remove();};
  }, [selection]);

  useEffect(() => {
    const instance = map.current, element = container.current;
    if (!instance || !element || !pickMode || !catalog) return;
    element.classList.add('selecting-region');
    const cancel = (event: KeyboardEvent) => {if (event.key === 'Escape') setPickMode(null);};
    window.addEventListener('keydown', cancel);
    if (pickMode === 'point') {
      const pick = (event: L.LeafletMouseEvent) => {
        const bounded = clampMapLatLng(event.latlng);
        try {applySelection(pointSelection(String(bounded.lng), String(bounded.lat), pointSpan, catalog.grid_degrees, catalog.bbox));}
        catch (error) {setSpatialError((error as Error).message); setPickMode(null);}
      };
      instance.on('click', pick);
      return () => {instance.off('click', pick); element.classList.remove('selecting-region'); window.removeEventListener('keydown', cancel);};
    }
    const wasDragging = instance.dragging.enabled();
    instance.dragging.disable();
    let start: L.LatLng | null = null, startPixel: {x: number; y: number} | null = null;
    let preview: L.Rectangle | null = null;
    const down = (event: PointerEvent) => {
      if (event.button !== 0 || (event.target as Element).closest('.leaflet-control, .legend, .map-caption')) return;
      event.preventDefault(); event.stopPropagation(); element.setPointerCapture(event.pointerId);
      start = clampMapLatLng(instance.mouseEventToLatLng(event)); startPixel = {x: event.clientX, y: event.clientY};
      preview?.remove(); preview = L.rectangle(L.latLngBounds(start, start), {pane: 'selection', color: '#168e9f', weight: 2, fillOpacity: .12, interactive: false}).addTo(instance);
    };
    const move = (event: PointerEvent) => {if (start) preview?.setBounds(L.latLngBounds(start, clampMapLatLng(instance.mouseEventToLatLng(event))));};
    const up = (event: PointerEvent) => {
      if (!start || !startPixel) return;
      const end = clampMapLatLng(instance.mouseEventToLatLng(event));
      const bigEnough = Math.abs(event.clientX - startPixel.x) >= 5 && Math.abs(event.clientY - startPixel.y) >= 5;
      const box = [Math.min(start.lng, end.lng), Math.min(start.lat, end.lat), Math.max(start.lng, end.lng), Math.max(start.lat, end.lat)].map(value => Number(value.toFixed(6))) as BBox;
      start = null; startPixel = null; preview?.remove(); preview = null;
      if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
      if (!bigEnough) {setSpatialError('请拖出有宽度和高度的矩形区域。'); return;}
      try {applySelection({bbox: parseBounds(toDraft(box))}, false);}
      catch (error) {setSpatialError((error as Error).message); setPickMode(null);}
    };
    const cancelled = () => setPickMode(null);
    element.addEventListener('pointerdown', down, true); element.addEventListener('pointermove', move);
    element.addEventListener('pointerup', up); element.addEventListener('pointercancel', cancelled);
    return () => {
      preview?.remove(); element.classList.remove('selecting-region'); if (wasDragging) instance.dragging.enable();
      element.removeEventListener('pointerdown', down, true); element.removeEventListener('pointermove', move);
      element.removeEventListener('pointerup', up); element.removeEventListener('pointercancel', cancelled);
      window.removeEventListener('keydown', cancel);
    };
  }, [pickMode, catalog, pointSpan]);

  useEffect(() => {
    envGroup.current?.clearLayers(); effortGroup.current?.clearLayers();
    setHoveredCell(null);
    hoverIndex.current = sample && catalog ? {grid: sample.grid_degrees, origin: catalog.bbox, cells: new Map(sample.cells.map(cell => [cell.cell_id, cell]))} : null;
    if (!sample || !catalog || !sample.cells.length) return;
    const range = catalog.scales[factor];
    if (envVisible && range && !temperature) new RasterLayer(sample.cells as RasterCell[], {pane: 'environment', field: factor, month: sample.month, grid: sample.grid_degrees, range, opacity: .9, showGrid: gridVisible}).addTo(envGroup.current!);
    if (temperature && selectedDeltaRange) new RasterLayer(temperature.cells as RasterCell[], {pane: 'environment', field: 'delta_sst_c', month: temperature.month, grid: temperature.grid_degrees, range: selectedDeltaRange, palette: deltaSstPalette, opacity: .92, showGrid: false, markField: 'candidate'}).addTo(envGroup.current!);
    if (effortVisible && catalog.scales.fishing_hours) new RasterLayer(sample.cells as RasterCell[], {pane: 'effort', field: 'fishing_hours', month: sample.month, grid: sample.grid_degrees, range: {min: 0, max: catalog.scales.fishing_hours.max}, opacity: effortOpacity, logarithmic: true, showGrid: gridVisible, effortMode, showZero: showZeroEffort}).addTo(effortGroup.current!);
  }, [sample, catalog, factor, envVisible, effortVisible, effortOpacity, gridVisible, effortMode, showZeroEffort, temperature, deltaBaseline, deltaPercentile]);
  const selectedFactor = factors.find(f => f.field === factor)!;
  const range = catalog?.scales[factor];
  const effortMax = catalog?.scales.fishing_hours?.max ?? 0;
  const effortMiddle = Math.expm1(Math.log1p(effortMax) / 2);
  const positiveEffortCount = sample?.cells.filter(cell => cell.fishing_hours !== null && cell.fishing_hours > 0).length ?? 0;
  const index = catalog?.months.indexOf(month) ?? 0;
  const envCount = sample?.summary.environment_valid_cells[factor] ?? 0;
  const mean = sample ? sample.cells.flatMap(c => typeof c[factor] === 'number' ? [c[factor] as number] : []) : [];
  const meanValue = mean.length ? mean.reduce((a, b) => a + b, 0) / mean.length : null;
  const dateRange = catalog ? `${catalog.months[0].replace('-', '.')} — ${catalog.months.at(-1)!.replace('-', '.')}` : '载入中';
  const scopeLabel = selection?.point ? `${coordinateLabel(selection.point.longitude, 'longitude')} / ${coordinateLabel(selection.point.latitude, 'latitude')}` : '所选矩形区域';
  const emptyMessage = sample && !sample.cells.length ? sample.coverage_status === 'outside' ? '所选位置不在当前样例覆盖范围内' : '范围内暂无网格中心，请扩大选区' : '';
  // Every number below comes from the API response; the UI never recomputes a threshold.
  const delta = temperature ? {
    range: temperature.scale,
    threshold: temperature.reference.threshold_abs_delta_c,
    referenceLabel: temperature.reference.label,
    referenceMean: temperature.reference.regional_mean_sst_c,
    valid: temperature.summary.valid_delta_cells,
    missing: temperature.cells.length - temperature.summary.valid_delta_cells,
    candidates: temperature.summary.candidate_cells,
    paired: temperature.summary.paired_cells,
    candidateShare: temperature.summary.candidate_known_cell_share,
    effortShare: temperature.summary.candidate_effort_share,
    concentration: temperature.summary.effort_concentration_ratio,
    deltaCorrelation: temperature.summary.pearson_delta_log_effort,
    magnitudeCorrelation: temperature.summary.pearson_abs_delta_log_effort,
    scaleNote: deltaBaseline === 'regional' ? '同一月份完整覆盖域等权均值作参考' : '同一网格上一日历月作参考',
    previousMonth: temperature.reference.previous_month,
  } : null;
  const deltaCellValue = (cell: Cell | null) => cell ? (typeof cell.delta_sst_c === 'number' ? number(cell.delta_sst_c, 2) : '无数据') : '—';
  function sendMessage(input = draft) {
    const text = input.trim();
    if (!text || !catalog) return;
    const command = interpretCommand(text, month);
    let reply = '';
    if (command.type === 'month') {
      if (catalog.months.includes(command.month)) {
        setMonth(command.month);
        reply = `已切换到 ${command.month}。地图和当前数据会同步更新。`;
      } else reply = `当前样例可浏览 ${dateRange}，没有 ${command.month} 的数据。`;
    } else if (command.type === 'factor') {
      const item = factors.find(f => f.field === command.field)!;
      if (catalog.environment_fields.includes(command.field)) {
        setFactor(command.field); setEnvVisible(true);
        if (command.solo) setEffortVisible(false);
        reply = `已显示${item.name}图层${command.solo ? '，并隐藏捕捞努力量' : ''}。图例使用整个样例时间范围的统一色标。`;
      } else reply = `当前样例尚未接入${item.name}。`;
    } else if (command.type === 'layers') {
      if (command.environment !== undefined) setEnvVisible(command.environment);
      if (command.effort !== undefined) setEffortVisible(command.effort);
      reply = [command.environment !== undefined ? `环境图层已${command.environment ? '显示' : '隐藏'}` : '', command.effort !== undefined ? `捕捞努力量已${command.effort ? '显示' : '隐藏'}` : ''].filter(Boolean).join('，') + '。';
    } else if (command.type === 'delta') {
      const nextVisible = command.visible ?? true;
      setDeltaVisible(nextVisible);
      const nextBaseline = command.baseline ?? deltaBaseline;
      if (command.baseline) setDeltaBaseline(command.baseline);
      if (!nextVisible) reply = '海表温差图层已隐藏，地图回到所选环境要素图层。';
      else if (!temperature && !temperatureResponse) reply = `正在按${nextBaseline === 'regional' ? '同月全区域均值' : '同网格上一月'}基准计算 ${month} 的温差场，稍后可在图例与“当前数据”中核对阈值与读数。`;
      else reply = `${month}，参考基准为「${delta?.referenceLabel ?? (nextBaseline === 'regional' ? '同月全区域均值' : '同网格上一月')}」；|ΔSST| ≥ ${number(delta?.threshold ?? null, 2)} °C 的候选网格 ${delta?.candidates ?? '—'} 个（有效 ${delta?.valid ?? '—'} 格，缺测 ${delta?.missing ?? '—'} 格）。缺测网格不绘制，也不按 0 处理。`;
    } else if (command.type === 'reset') {
      returnToSelection();
      reply = '已返回当前所选范围的地图视野。';
    } else if (command.type === 'unavailable') {
      reply = command.feature === 'energy' ? '当前 GFW 月度数据没有能耗、功率或燃油字段，能耗保持为空，不能直接从时长推算。'
        : command.feature === 'analysis' ? '当前还没有相关性分析结果。你可以先叠加环境与努力量图层，浏览它们的空间分布。'
        : '当前提供的是历史数据浏览，尚无预报结果。';
    } else if (command.type === 'query') {
      if (error) reply = `当前选区未能读取数据：${error}。请调整范围后重试。`;
      else if (loading || !sample) reply = '正在读取所选月份与范围的数据，待当前数据更新后再查询即可。';
      else {
        const item = factors.find(f => f.field === command.field);
        if (item) {
          const values = sample.cells.flatMap(c => typeof c[item.field] === 'number' ? [c[item.field] as number] : []);
          reply = values.length ? `${sample.month}，当前所选范围内的${item.short}有效网格均值为 ${number(values.reduce((a, b) => a + b, 0) / values.length, 2)} ${item.unit}，基于 ${values.length} 个有效网格。` : `该月份、该范围的${item.short}没有有效记录。`;
        } else reply = sample.summary.fishing_hours === null ? `${sample.month}，当前所选范围没有 GFW 时长记录，不能解释为零捕捞。` : `${sample.month}，当前所选范围的总捕捞时长为 ${number(sample.summary.fishing_hours, 4)} 船舶小时。${sample.summary.cells} 个网格中，${sample.summary.observed_cells} 个有 GFW 记录。无记录不能解释为零捕捞。`;
      }
    } else reply = '目前支持月份切换、图层控制和当前区域数据查询。试试“切换到三月”“只看叶绿素”或“当前捕捞时长是多少”。';
    setMessages(previous => [...previous, {role: 'user', text}, {role: 'assistant', text: reply}]);
    setDraft('');
  }
  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><div className="brand-icon">≈</div><div><strong>海洋渔场观测台</strong><span>环境要素与捕捞努力量</span></div></div>
      <nav aria-label="任务栏"><span className="nav-active">地图工作台</span><span className="nav-muted">相关性分析</span><span className="nav-muted">努力量预报</span></nav>
      <div className="header-status"><span className="status-dot"/>真实历史样例<span className="prototype-tag">基础浏览</span></div>
    </header>
    <div className="workspace">
      <aside className="sidebar" aria-label="操作与数据面板">
        <div className="panel-heading"><div className="panel-symbol">☷</div><div><h2>观测配置</h2><p>选择范围、时间与图层</p></div><span className="panel-index">01</span></div>
        <div className="sidebar-scroll">
        <div className="section-kicker">数据覆盖 / COVERAGE</div><h1>{catalog?.name ?? '载入中'}</h1>
        <p className="subtle">在已准备的数据覆盖内选择位置</p>
        <div className="region-box"><span>{catalog ? `${coordinateLabel(catalog.bbox[0], 'longitude')} — ${coordinateLabel(catalog.bbox[2], 'longitude')}` : '—'}</span><span>{catalog ? `${coordinateLabel(catalog.bbox[1], 'latitude')} — ${coordinateLabel(catalog.bbox[3], 'latitude')}` : '—'}</span><b>{catalog?.grid_degrees ?? '—'}° 网格 · 月度</b></div>
        {!!catalog?.regions?.length && <label className="region-preset-label" htmlFor="region-preset">海域快捷选择<select id="region-preset" aria-label="海域快捷选择" value={regionChoice} onChange={e => {const region = catalog.regions?.find(item => item.id === e.target.value); applySelection({bbox: region?.bbox ?? catalog.initial_bbox});}}><option value="all">完整数据区域 · {catalog.bbox[2] - catalog.bbox[0]}° × {catalog.bbox[3] - catalog.bbox[1]}°</option>{catalog.regions.map((region, i) => <option value={region.id} key={region.id}>{i + 1} · {region.name}</option>)}<option value="custom" disabled>自定义选区</option></select></label>}
        <div className="section-title"><h2>时间范围</h2><span>{catalog?.months.length ?? '—'} 个月</span></div>
        <label className="date-label" htmlFor="month-select">观测月份</label>
        <div className="date-row"><button aria-label="上一个月" disabled={!catalog || index <= 0} onClick={() => setMonth(catalog!.months[index - 1])}>‹</button><select id="month-select" value={month} onChange={e => setMonth(e.target.value)} disabled={!catalog}>{catalog?.months.map(m => <option key={m} value={m}>{m.replace('-', ' 年 ')} 月</option>)}</select><button aria-label="下一个月" disabled={!catalog || index >= catalog.months.length - 1} onClick={() => setMonth(catalog!.months[index + 1])}>›</button></div>
        <p className="date-footnote">{dateRange}</p>
        <div className="section-title"><h2>图层控制</h2><span>LAYERS</span></div>
        <label className="layer-control"><input type="checkbox" aria-label="显示环境图层" checked={envVisible} onChange={e => setEnvVisible(e.target.checked)}/><span className="layer-mark env-mark"/><span>环境要素<small>Copernicus Marine</small></span></label>
        <select className="factor-select" aria-label="环境要素" value={factor} onChange={e => setFactor(e.target.value)}>{factors.map(f => <option key={f.field} value={f.field} disabled={!catalog?.environment_fields.includes(f.field)}>{f.name}{catalog && !catalog.environment_fields.includes(f.field) ? '（未接入）' : ''}</option>)}</select>
        <label className="layer-control"><input type="checkbox" aria-label="显示捕捞努力量" checked={effortVisible} onChange={e => setEffortVisible(e.target.checked)}/><span className="layer-mark effort-mark"/><span>捕捞努力量<small>GFW · 捕捞时长</small></span></label>
        <select className="factor-select effort-style" aria-label="努力量显示方式" value={effortStyle} disabled={!effortVisible} onChange={e => setEffortStyle(e.target.value as 'auto' | 'symbols' | 'raster')}><option value="auto">自动：叠加符号 / 独立栅格</option><option value="symbols">紫色比例符号</option><option value="raster">高对比紫色栅格</option></select>
        <p className="effort-mode-note">{effortMode === 'symbols' ? '紫色符号标记正努力量，大小表示时长。' : '紫色深浅表示时长，灰色表示已知零值。'}</p>
        <label className="layer-control compact-control"><input type="checkbox" aria-label="显示零时长记录" checked={showZeroEffort} disabled={!effortVisible} onChange={e => setShowZeroEffort(e.target.checked)}/><span>显示零时长记录</span></label>
        <label className="opacity-control" htmlFor="effort-opacity">努力量不透明度 <span>{Math.round(effortOpacity * 100)}%</span><input id="effort-opacity" aria-label="努力量不透明度" type="range" min="0.1" max="1" step="0.01" value={effortOpacity} disabled={!effortVisible} onChange={e => setEffortOpacity(Number(e.target.value))}/></label>
        <label className="layer-control compact-control"><input type="checkbox" aria-label="显示网格边界" checked={gridVisible} onChange={e => setGridVisible(e.target.checked)}/><span>0.1° 网格边界</span></label>
        <label className="layer-control"><input type="checkbox" aria-label="显示海表温差图层" checked={deltaVisible} onChange={e => setDeltaVisible(e.target.checked)}/><span className="layer-mark delta-mark"/><span>海表温差 |ΔSST|<small>Copernicus GLORYS 表层势温</small></span></label>
        {deltaVisible && <div className="delta-controls">
          <label className="extent-label" htmlFor="delta-baseline">温差参考基准<select id="delta-baseline" aria-label="温差参考基准" value={deltaBaseline} onChange={e => setDeltaBaseline(e.target.value as 'regional' | 'previous')}><option value="regional">同月全区域均值</option><option value="previous">同网格上一月</option></select></label>
          <label className="opacity-control" htmlFor="delta-percentile">候选阈值分位 <span>P{deltaPercentile}</span><input id="delta-percentile" aria-label="候选阈值分位" type="range" min="50" max="95" step="5" value={deltaPercentile} onChange={e => setDeltaPercentile(Number(e.target.value))}/></label>
          <p className="effort-mode-note">{delta?.scaleNote ?? '正在计算温差场…'}{delta?.previousMonth ? `（对比 ${delta.previousMonth}）` : ''}。参考域固定为全部六个 2°×2° 格，不随当前选区重算。</p>
        </div>}
        <label className="layer-control layer-disabled"><input type="checkbox" aria-label="显示海洋锋面" disabled/><span className="layer-mark missing-mark"/><span>海洋锋面<small>{catalog?.fronts?.reason ?? '独立锋面折线未提供'}</small></span></label>
        <label className="layer-control layer-disabled"><input type="checkbox" disabled/><span className="layer-mark missing-mark"/><span>能耗<small>源数据未提供 · 待补充</small></span></label>
        <section className="spatial-selector" aria-label="经纬度与区域选择">
          <div className="section-title"><h2>位置与范围</h2><span>WGS84</span></div>
          <div className="selection-modes" role="group" aria-label="选择方式"><button aria-pressed={selectionMode === 'point'} className={selectionMode === 'point' ? 'active' : ''} onClick={() => {setSelectionMode('point'); setPickMode(null); setSpatialError('');}}>经纬度定位</button><button aria-pressed={selectionMode === 'area'} className={selectionMode === 'area' ? 'active' : ''} onClick={() => {setSelectionMode('area'); setPickMode(null); setSpatialError('');}}>矩形区域</button></div>
          {selectionMode === 'point' ? <form className="spatial-form" noValidate onSubmit={e => {e.preventDefault(); applyCoordinates();}}>
            <div className="coordinate-inputs"><label htmlFor="longitude-input">经度<input id="longitude-input" type="number" min={WORLD_BOUNDS[0]} max={WORLD_BOUNDS[2]} step="any" value={longitude} onChange={e => setLongitude(e.target.value)}/></label><label htmlFor="latitude-input">纬度<input id="latitude-input" type="number" min={MAP_LATITUDE_MIN} max={MAP_LATITUDE_MAX} step="any" value={latitude} onChange={e => setLatitude(e.target.value)}/></label></div>
            <label className="extent-label" htmlFor="point-span">加载范围<select id="point-span" aria-label="加载范围" value={pointSpan} onChange={e => setPointSpan(Number(e.target.value))}><option value={catalog?.grid_degrees ?? .1}>单个 {catalog?.grid_degrees ?? .1}° 网格</option><option value="0.5">0.5° × 0.5°</option><option value="1">1° × 1°</option></select></label>
            <div className="spatial-actions"><button className="apply-area" type="submit" disabled={!catalog}>定位并加载</button><button type="button" aria-pressed={pickMode === 'point'} onClick={() => {setSpatialError(''); setPickMode(pickMode === 'point' ? null : 'point');}} disabled={!catalog}>地图点选</button></div>
          </form> : <form className="spatial-form" noValidate onSubmit={e => {e.preventDefault(); try {applySelection({bbox: parseBounds(boundsDraft)});} catch (error) {setSpatialError((error as Error).message);}}}>
            <div className="coordinate-inputs">{(['west', 'east', 'south', 'north'] as const).map(key => <label key={key} htmlFor={`bound-${key}`}>{({west: '西界经度', east: '东界经度', south: '南界纬度', north: '北界纬度'})[key]}<input id={`bound-${key}`} type="number" min={key === 'west' || key === 'east' ? WORLD_BOUNDS[0] : MAP_LATITUDE_MIN} max={key === 'west' || key === 'east' ? WORLD_BOUNDS[2] : MAP_LATITUDE_MAX} step="any" value={boundsDraft[key]} onChange={e => setBoundsDraft(previous => ({...previous, [key]: e.target.value}))}/></label>)}</div>
            <div className="spatial-actions"><button className="apply-area" type="submit" disabled={!catalog}>应用区域</button><button type="button" onClick={resetSelection} disabled={!catalog}>恢复样例</button></div>
          </form>}
          <p className="coordinate-hint">西经、南纬填负数；地图纬度范围 −85.051129°～85.051129°；只读取所选范围的网格。</p>
          {spatialError && <p className="spatial-error" role="alert">{spatialError}</p>}
          {sample?.coverage_status === 'partial' && <p className="coverage-note">部分范围超出覆盖，仅加载有数据的交集。</p>}
          <div className="loaded-region" data-testid="loaded-region"><span>{scopeLabel}</span><b>{sample ? `${sample.cells.length} 个网格已加载` : loading ? '读取选区…' : '未加载'}</b></div>
        </section>
        <div className="source-note"><span className="status-dot"/>GFW v3 · Copernicus Marine<p>AIS 无记录不能解释为零捕捞；捕捞时长也不等于渔获量。</p></div>
        </div>
      </aside>
      <main className="map-column">
        <div className="map-toolbar"><div><span className="status-dot"/><strong>空间观测</strong><span className="map-date">{month || '载入中'}</span></div><div className="map-selection-tools"><button className={pickMode ? 'picking' : ''} onClick={() => {setSpatialError(''); setPickMode(pickMode ? null : selectionMode);}} disabled={!catalog}>{pickMode ? '取消选择' : selectionMode === 'point' ? '点选经纬度' : '框选区域'}</button><button onClick={returnToSelection}>↗ 返回选区</button></div></div>
        <div className="map-stage"><div ref={container} className="map" data-testid="base-map"/>
          <div className="map-caption"><span>02 / 地图视图</span><strong>{catalog?.name ?? '载入中'}</strong><small>{delta ? '海表温差 |ΔSST|' : envVisible ? selectedFactor.short : '基础地图'}{effortVisible ? ' + 捕捞努力量' : ''}</small></div>
          {(loading || error || landState === 'error') && <div className={`map-message ${error ? 'error' : ''}`} role="status">{error || (landState === 'error' ? '基础地图轮廓加载失败' : '正在读取月度样例…')}</div>}
          {emptyMessage && <div className="map-message no-coverage" role="status">{emptyMessage}</div>}
          {deltaVisible && !delta && !loading && <div className="map-message no-coverage" role="status">正在计算所选范围的海表温差…</div>}
          {pickMode && <div className="map-pick-hint" role="status">{pickMode === 'point' ? '点击地图选择经纬度' : '按住并拖拽，框选矩形区域'} · Esc 取消</div>}
          {hoveredCell && (envVisible || effortVisible || delta) && !pickMode && <div className="cell-inspector" data-testid="cell-inspector"><strong>{hoveredCell.month} · 单网格</strong><span>{coordinateLabel(hoveredCell.longitude, 'longitude')} / {coordinateLabel(hoveredCell.latitude, 'latitude')}</span><p>{delta ? '海表温差 ΔSST' : selectedFactor.short} <b>{delta ? deltaCellValue(hoveredCell) : number(typeof hoveredCell[factor] === 'number' ? hoveredCell[factor] as number : null, 3)}</b> {delta ? '°C' : selectedFactor.unit}</p>{delta && <p>参考海温 <b>{number(typeof hoveredCell.reference_sst_c === 'number' ? hoveredCell.reference_sst_c as number : null, 2)}</b> °C</p>}<p>捕捞时长 <b>{number(hoveredCell.fishing_hours, 3)}</b> 船舶小时/月</p><small>{hoveredCell.fishing_hours === null ? 'GFW 无记录' : hoveredCell.fishing_hours === 0 ? 'GFW 源记录明确为零' : 'GFW 模型推断时长'}{delta && delta.threshold !== null ? hoveredCell.delta_sst_c === null ? ' · 温差无数据' : ` · |ΔSST| ${Math.abs(hoveredCell.delta_sst_c as number) >= delta.threshold ? '达到' : '未达到'}候选阈值` : ''}</small></div>}
          <div className="legend" data-testid="legend"><div className="legend-title">图例 <span>六个月统一色标</span></div>
            {envVisible && range && !delta && <><p>{selectedFactor.short} <small>{selectedFactor.unit}</small></p><div className="color-ramp"/><div className="legend-range"><span>{number(range.min, 2)}</span><span>{number(range.max, 2)}</span></div></>}
            {delta && deltaVisible && <div className="effort-raster-legend" data-testid="delta-legend"><p>海表温差 ΔSST <small>°C · 表层势温</small></p>
              <div className="delta-ramp"/>
              <div className="legend-range"><span>{number(delta.range.min, 2)} 偏冷</span><span>0</span><span>偏暖 {number(delta.range.max, 2)}</span></div>
              <span className="delta-legend-note">色标以 0 为中心、覆盖全部六个月，不随选区重新拉伸。</span>
              <div className="delta-candidate-key"><b/>黑色描边：|ΔSST| ≥ P{deltaPercentile}（{number(delta.threshold, 2)} °C）的候选网格</div>
              <div className="delta-coverage">有效 {delta.valid} / {temperature?.cells.length ?? '—'} 格<span className="delta-missing"> · 缺测 {delta.missing} 格（不绘制，不视为 0）</span></div>
              <span className="scale-note">参考：{delta.referenceLabel}（{delta.referenceMean === null ? '—' : `${number(delta.referenceMean, 2)} °C`}）</span>
            </div>}
            {effortVisible && <div className="effort-raster-legend"><p>捕捞时长 <small>船舶小时/月</small></p>
              {effortMode === 'symbols' ? <><div className="effort-symbol-key">{[1, 10, 100].map(value => <div key={value}><svg width="32" height="30" viewBox="0 0 32 30" aria-hidden="true"><circle cx="16" cy="15" r={effortSymbolRadius(value, effortMax, symbolCellPixels)} fill="#7c3aed" stroke="white" strokeWidth="3.4"/><circle cx="16" cy="15" r={effortSymbolRadius(value, effortMax, symbolCellPixels)} fill="#7c3aed" stroke="#3b136b" strokeWidth="1.5"/></svg><span>{value} h</span></div>)}</div><span className="scale-note">对数尺寸 · 网格中心符号</span></> : <><div className="effort-ramp"/><div className="legend-range"><span>0+</span><span>{number(effortMiddle, 1)}</span><span>{number(effortMax, 1)}</span></div><span className="scale-note">对数色标 · 同一半年固定</span></>}
              <div className="effort-activity-count">正努力量：{positiveEffortCount} 个网格</div>
              <div className="missing-key"><i/>无记录：透明 <b className={effortMode === 'symbols' ? 'zero-cross' : ''}/>{showZeroEffort ? effortMode === 'symbols' ? '零时长：灰叉' : '零时长：灰色' : '零时长：未绘制'}</div>
            </div>}
            {!envVisible && !effortVisible && !delta && <p>当前仅显示基础地图</p>}
          </div>
        </div>
        <div className="map-status"><span>{cursor}</span><span>基础地图：{landState === 'ready' ? '离线海岸线' : landState === 'error' ? '加载失败' : '载入中'}<i/>EPSG:4326</span></div>
        <section className="map-summary" aria-label="当前数据">
          <div className="summary-title"><h2>当前数据</h2><p className="inspector-date">{sample?.month ?? month} · 所选范围汇总</p><span className="live-label">{loading ? '载入中' : error ? '读取失败' : emptyMessage ? '无数据' : '已就绪'}</span></div>
          <div className="summary-metrics"><div><span>总捕捞时长 <small>船舶小时 / 月</small></span><strong data-testid="effort-total">{number(sample?.summary.fishing_hours)}</strong></div><div><span>{delta ? '温差候选网格' : `${selectedFactor.short}均值`} <small>{delta ? `|ΔSST| ≥ ${number(delta.threshold, 2)} °C` : selectedFactor.unit}</small></span><strong data-testid="delta-candidates">{delta ? `${delta.candidates} / ${temperature?.cells.length ?? '—'}` : number(meanValue, 2)}</strong></div><div className="summary-coverage"><span>有效网格 / {sample?.summary.cells ?? '—'}</span><p>GFW <b>{sample?.summary.observed_cells ?? '—'}</b><i/>环境 <b>{sample ? (delta ? delta.valid : envCount) : '—'}</b></p></div></div>
          {delta && <p className="delta-evidence" data-testid="delta-evidence">候选网格占已知努力量网格 <b>{delta.candidateShare === null ? '—' : `${(delta.candidateShare * 100).toFixed(1)}%`}</b>，占这些网格的捕捞时长 <b>{delta.effortShare === null ? '—' : `${(delta.effortShare * 100).toFixed(1)}%`}</b>（集中度 {delta.concentration === null ? '—' : delta.concentration.toFixed(2)}）；ΔSST 与 log(1+时长) 的样本内相关 r = <b>{delta.deltaCorrelation === null ? '—' : delta.deltaCorrelation.toFixed(3)}</b>，|ΔSST| 与 log(1+时长) r = <b>{delta.magnitudeCorrelation === null ? '—' : delta.magnitudeCorrelation.toFixed(3)}</b>（{delta.paired} 个已配对网格）。以上为样本内描述统计，不是鱼类适宜性结论，也不是预报验证。</p>}
        </section>
        <div className="timeline"><div><span className="section-kicker">月度浏览</span><strong>{catalog?.months[0].slice(0, 4) ?? '—'} 年 · 历史样例</strong></div><div className="month-buttons">{catalog?.months.map(m => <button aria-label={`切换到${m}`} className={month === m ? 'selected' : ''} key={m} onClick={() => setMonth(m)}>{Number(m.slice(-2))} 月<span/></button>)}</div></div>
      </main>
      <aside className="conversation" aria-label="对话交互面板">
        <div className="panel-heading"><div className="panel-symbol chat-symbol">✦</div><div><h2>对话助手</h2><p>用一句话，驱动你的观测</p></div><button className="clear-chat" aria-label="清空对话" title="清空对话" onClick={() => setMessages([])}>↺</button></div>
        <div className="chat-context"><span className="status-dot"/><span>{month || '等待数据'} · {selectedFactor.short}</span><span className="context-tag">{sample ? `${sample.cells.length} 格` : '读取选区'}</span></div>
        <div className="chat-history" ref={chatHistory} role="log" aria-label="对话记录" aria-live="polite">
          <div className="assistant-intro"><div className="intro-orbit">≈</div><span className="section-kicker">开始一次观测</span><h2>你想先看什么？</h2><p>左侧选择条件，中间观察分布。<br/>也可以在这里直接告诉我。</p></div>
          <div className="welcome-message"><span className="assistant-avatar">✦</span><div><strong>观测助手</strong><p>我可以帮你切换月份、显示或隐藏图层，以及读取当前区域的数据。</p><span>支持浏览指令 · 相关性与预报待后续接入</span></div></div>
          {messages.map((message, i) => <div key={i} className={`chat-message ${message.role}`}><span className="message-avatar">{message.role === 'assistant' ? '✦' : '我'}</span><div className="message-bubble">{message.text}</div></div>)}
        </div>
        <div className="suggestions"><span>试试这样说</span><div><button disabled={!catalog} onClick={() => sendMessage('切换到三月')}>切换到三月 ↗</button><button disabled={!catalog} onClick={() => sendMessage('看看温差候选渔场')}>看看温差候选渔场 ↗</button><button disabled={!sample || loading} onClick={() => sendMessage('当前捕捞时长是多少')}>当前捕捞时长 ↗</button></div></div>
        <form className="chat-composer" onSubmit={e => {e.preventDefault(); sendMessage();}}>
          <label className="sr-only" htmlFor="chat-input">对话指令</label><textarea id="chat-input" value={draft} onChange={e => setDraft(e.target.value)} placeholder="例如：切换到三月" rows={2} maxLength={500} disabled={!catalog} onKeyDown={e => {if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault(); sendMessage();}}}/>
          <div className="composer-tools"><span>浏览指令原型</span><button type="submit" aria-label="发送指令" disabled={!draft.trim() || !catalog}>发送 <span>↑</span></button></div>
        </form>
        <div className="chat-footnote">Enter 发送 · Shift + Enter 换行</div>
      </aside>
    </div>
    <footer className="footer"><span>第一阶段范围：单海域 · 月度网格 · 基础环境浏览</span><span>真实历史数据 · GFW v3 / Copernicus Marine</span></footer>
  </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
