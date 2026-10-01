/** Deterministic net-profit calculation shared by teacher calibration inputs. */
export function calculateNetProfit(revenue, operatingCost, operatingExpense) {
  if (![revenue, operatingCost, operatingExpense].every(Number.isFinite)) return null
  return revenue - operatingCost - operatingExpense
}
