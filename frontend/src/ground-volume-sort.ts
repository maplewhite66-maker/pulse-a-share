export type GroundSort = 'score' | 'win_rate' | 'mean_return' | 'max_drawdown';
type Ranked = {rank: number} & Record<GroundSort, number | null>;

export function sortGroundVolume<T extends Ranked>(rows: readonly T[], key: GroundSort): T[] {
  return [...rows].sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av == null || bv == null) {
      return av == null && bv == null ? a.rank - b.rank : av == null ? 1 : -1;
    }
    return (key === 'max_drawdown' ? av - bv : bv - av) || a.rank - b.rank;
  });
}
