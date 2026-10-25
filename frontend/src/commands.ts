export type BrowseCommand =
  | {type: 'month'; month: string}
  | {type: 'factor'; field: string; solo: boolean}
  | {type: 'layers'; environment?: boolean; effort?: boolean}
  | {type: 'delta'; visible?: boolean; baseline?: 'regional' | 'previous'}
  | {type: 'query'; field?: string}
  | {type: 'reset'}
  | {type: 'unavailable'; feature: 'energy' | 'analysis' | 'forecast'}
  | {type: 'help'};

// This is a local browser command vocabulary, with no model or remote chat call.
export function interpretCommand(input: string, currentMonth: string): BrowseCommand {
  const chineseMonths: Record<string, number> = {一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, 十一: 11, 十二: 12};
  const text = input.trim().toLowerCase().replace(/\s+/g, '').replace(/(十一|十二|[一二三四五六七八九十])月/g, (_, value: string) => `${chineseMonths[value]}月`);
  if (/能耗|燃油|功率|kwh/.test(text)) return {type: 'unavailable', feature: 'energy'};
  if (/相关性|相关系数|分析关系/.test(text)) return {type: 'unavailable', feature: 'analysis'};
  if (/预报|预测|未来/.test(text)) return {type: 'unavailable', feature: 'forecast'};
  const fullDate = text.match(/(20\d{2})[-/.年](\d{1,2})(?:月)?/);
  const shortDate = text.match(/(?:^|\D)(\d{1,2})月/);
  if (fullDate || shortDate) {
    const year = fullDate ? fullDate[1] : currentMonth.slice(0, 4);
    const month = fullDate ? fullDate[2] : shortDate![1];
    return {type: 'month', month: `${year}-${month.padStart(2, '0')}`};
  }
  if (/返回观测|复位|重置视野|回到海域/.test(text)) return {type: 'reset'};
  // The difference layer replaces the raw temperature layer; it is matched before the generic field rules.
  if (/温差|距平|温度梯度|候选渔场|候选区/.test(text)) return {type: 'delta', visible: !/隐藏|关闭|取消/.test(text), baseline: /上一月|上月|前一个月|环比|同网格/.test(text) ? 'previous' : /区域|均值|平均|同月/.test(text) ? 'regional' : undefined};
  if (/只看底图|仅看底图|只看基础地图|隐藏所有|关闭所有/.test(text)) return {type: 'layers', environment: false, effort: false};
  if (/显示所有|打开所有|恢复图层/.test(text)) return {type: 'layers', environment: true, effort: true};
  const field = /ssha|sla|海面高度距平/.test(text) ? 'ssha_m'
    : /ssh|海面高度/.test(text) ? 'ssh_m'
    : /叶绿素|chl/.test(text) ? 'chl_mg_m3'
    : /初级生产|生产力|npp/.test(text) ? 'npp_mg_c_m3_day'
    : /海温|温度|sst/.test(text) ? 'sst_c' : undefined;
  if (/多少|均值|平均|汇总|当前值|当前数据/.test(text)) return {type: 'query', field};
  if (/隐藏|关闭/.test(text)) {
    if (/努力量|捕捞/.test(text)) return {type: 'layers', effort: false};
    if (/环境|要素/.test(text) || field) return {type: 'layers', environment: false};
  }
  if (field) return {type: 'factor', field, solo: /只看|仅看/.test(text)};
  if (/努力量|捕捞/.test(text)) {
    if (/只看|仅看/.test(text)) return {type: 'layers', environment: false, effort: true};
    if (/显示|打开/.test(text)) return {type: 'layers', effort: true};
    return {type: 'query'};
  }
  if (/显示环境|打开环境/.test(text)) return {type: 'layers', environment: true};
  return {type: 'help'};
}
