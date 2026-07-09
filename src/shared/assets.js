const DECIMALS_MAP = {
  '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2': 18, // WETH
  '0x2170Ed0880ac9A755fd29B2688956BD959F933F8': 18, // WETH (BSC)
  '0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c': 8,  // WBTC (BSC)
  '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c': 18, // WBNB
  '0x55d398326f99059fF775485246999027B3197955': 18, // USDT (BSC)
  '0xdAC17F958D2ee523a2206206994597C13D831ec7': 6,  // USDT (ETH)
};

export function resolveDecimals(assetAddress) {
  const decimals = DECIMALS_MAP[assetAddress];
  if (decimals === undefined) {
    throw new Error(`resolveDecimals: unknown asset address "${assetAddress}" — add it to DECIMALS_MAP`);
  }
  return decimals;
}
