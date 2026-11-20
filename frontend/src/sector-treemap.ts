type Weighted = {amount: number | null | undefined};
type Rectangle = {left: number; top: number; width: number; height: number};

export function sectorTreemap<T extends Weighted>(sectors: T[]) {
  const weighted = sectors.flatMap(sector =>
    typeof sector.amount === 'number' && Number.isFinite(sector.amount) && sector.amount > 0
      ? [{sector, weight: sector.amount}]
      : [],
  ).sort((a, b) => b.weight - a.weight);
  const total = weighted.reduce((sum, {weight}) => sum + weight, 0);
  if (!Number.isFinite(total) || total <= 0) return [];

  function layout(items: typeof weighted, weight: number, rect: Rectangle): (Rectangle & {sector: T})[] {
    if (items.length === 1) return [{...rect, sector: items[0].sector}];

    let split = 1;
    let part = items[0].weight;
    while (split < items.length - 1 &&
      Math.abs(part + items[split].weight - weight / 2) < Math.abs(part - weight / 2)) {
      part += items[split++].weight;
    }
    const fraction = part / weight;
    const across = rect.width * 1.6 >= rect.height;
    const first = across
      ? {...rect, width: rect.width * fraction}
      : {...rect, height: rect.height * fraction};
    const rest = across
      ? {...rect, left: rect.left + first.width, width: rect.width - first.width}
      : {...rect, top: rect.top + first.height, height: rect.height - first.height};
    const remaining = items.slice(split);
    return [
      ...layout(items.slice(0, split), part, first),
      ...layout(remaining, remaining.reduce((sum, item) => sum + item.weight, 0), rest),
    ];
  }

  return layout(weighted, total, {left: 0, top: 0, width: 100, height: 100});
}
