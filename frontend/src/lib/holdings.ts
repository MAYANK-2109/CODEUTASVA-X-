// Rules shared by the Portfolio and Dashboard pages for reading saved holdings.

interface SavedHolding {
  name: string
  symbol?: string | null
  isin?: string | null
  buy_price?: number | string | null
  current_price?: number | string | null
}

// Rows an earlier version of the importer saved from a statement's header
// block. They have a label for a name and no symbol, ISIN or buy price.
const LEFTOVER_NAME =
  /^(name|unique client code|client (id|code|name)|pan|holdings? statement.*|summary|invested value|closing value|current value|unreali[sz]ed p&l|stock name|scrip name|total.*)$/i

export const isLeftover = (h: SavedHolding) =>
  !h.symbol?.trim() && !h.isin?.trim() && !(Number(h.buy_price) > 0) && LEFTOVER_NAME.test(h.name.trim())

/**
 * The price saved with a holding, when it is a real price. An old import
 * default wrote zero, or a copy of the buy price, where the price was unknown;
 * both are read back as unknown. Valuing such a holding at cost gives the same
 * total either way, so only the display changes.
 */
export const savedPrice = (h: SavedHolding): number | null => {
  const price = Number(h.current_price)
  const buy = Number(h.buy_price)
  if (!(price > 0)) return null
  if (buy > 0 && price === buy) return null
  return price
}
