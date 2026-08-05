/**
 * What the sales trend counts, said next to the trend.
 *
 * The chart and the "Shopify Sales" card measure different populations and both
 * are right: the card joins to the imported sheet and counts only SKUs found in
 * it, while the trend sums every SKU the store sold. On the live workspace that
 * is 570,435 against 1,352,080 — the chart totals more than twice the card sat
 * directly above it, and nothing on screen said why.
 *
 * The card already discloses its own scope ("units matched by SKU"); this is the
 * other half of that sentence. One component rather than the same paragraph
 * pasted into three pages, so the two explanations cannot drift apart.
 */

export function TrendScopeNote() {
  return (
    <div className="trend-scope">
      This trend shows <b>all Shopify sales</b> from your store. The Shopify Sales figure above
      counts only SKUs imported into StockSync Analytics.
    </div>
  );
}
