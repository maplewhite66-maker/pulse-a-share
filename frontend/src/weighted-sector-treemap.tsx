import {money, percent, tone, type Sector} from './data';
import {sectorTreemap} from './sector-treemap';

export function WeightedSectorTreemap({sectors, selected, onSelect}: {
  sectors: Sector[];
  selected: string | null;
  onSelect: (code: string) => void;
}) {
  const cells = sectorTreemap(sectors);
  const plotted = new Set(cells.map(({sector}) => sector.code));
  const missing = sectors.filter(sector => !plotted.has(sector.code));
  return <div className="sector-heat">
    <p className="micro">面积为榜内当日成交额占比，红涨绿跌，边框表示主力资金方向。</p>
    {cells.length > 0 && <div className="sector-treemap" aria-label="板块成交额热力图">
      {cells.map(({sector, left, top, width, height}) => {
        const movement = tone(sector.pct);
        const flow = tone(sector.main_net);
        const detail = `${sector.name}，涨跌幅 ${percent(sector.pct)}，主力净额 ${money(sector.main_net, true)}，成交额 ${money(sector.amount)}`;
        return <button key={sector.code} type="button" aria-label={detail} title={detail}
          aria-pressed={selected === sector.code}
          className={`sector-cell price-${movement} flow-${flow}`}
          style={{left: left + '%', top: top + '%', width: width + '%', height: height + '%'}}
          onClick={() => onSelect(sector.code)}>
          <strong>{sector.name}</strong><small>{percent(sector.pct)}</small><small>净额 {money(sector.main_net, true)}</small>
        </button>;
      })}
    </div>}
    {missing.length > 0 && <div className="sector-unweighted">
      <span className="micro">无有效成交额</span>
      {missing.map(sector => <button key={sector.code} type="button" className="btn"
        aria-pressed={selected === sector.code} onClick={() => onSelect(sector.code)}>{sector.name}</button>)}
    </div>}
  </div>;
}
