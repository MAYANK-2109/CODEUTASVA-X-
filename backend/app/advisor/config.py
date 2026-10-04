"""Settings for the sector and stock suggestion engine. Every threshold the
engine applies is here, so none is hidden in the scoring code."""

# The sectors that are ranked: those with at least two liquid stocks in the app's sector map.
RANKED_SECTORS = ["Energy", "Utilities", "Banking", "Financials", "IT", "FMCG", "Auto", "Pharma", "Metals",
                  "Infrastructure", "Cement"]
DEFENSIVE_SECTORS = {"FMCG", "Pharma", "Utilities"}

# Sector composite, out of 100. Portfolio fit carries the most weight.
SECTOR_WEIGHTS = {"fit": 40, "evidence": 25, "macro": 20, "trend": 15}
# Stock composite inside a sector, out of 100.
STOCK_WEIGHTS = {"improvement": 30, "quality": 20, "valuation": 15, "momentum": 15, "sentiment": 10, "risk": 10}

LOOKBACK_SESSIONS = 750          # three years of daily returns behind every statistic
MIN_SESSIONS = 250               # less shared history than this is "insufficient data"
STALE_AFTER_DAYS = 7             # prices older than this are not analysed
CANDIDATE_SHARE = 0.05           # a sector or stock is tested at 5% of the portfolio
MAX_NEW_WEIGHT = 0.05            # cap on any one addition
MAX_TOTAL_NEW = 0.15             # all additions together
MAX_TRIM_OF_HOLDING = 0.5        # a holding is never cut by more than half to pay for them
MAX_SECTORS = 3
PICKS_PER_SECTOR = 3
RISK_FREE_RATE = 0.065           # annual, an assumption used only inside Sharpe ratios

THIN_ANALOGS = 3                 # fewer comparable past events than this is thin evidence
THIN_HEADLINES = 5
THIN_PENALTY = 0.8               # evidence score is multiplied by this when it is thin
EXTENDED_ABOVE_200D = 0.15       # a sector this far above its 200-day average is extended
EXTENDED_PENALTY = 0.7           # trend score is multiplied by this when it is
CLUSTER_CORRELATION = 0.6
MAX_CORRELATION = 0.85           # a stock that moves this closely with the portfolio adds concentration

MIN_MARKET_CAP = 2e11            # Rs 20,000 crore
MIN_DAILY_VALUE = 2.5e8          # Rs 25 crore traded a day
HEADLINES_PER_SECTOR = 8

WALK_FORWARD_LOOKBACK = 500
WALK_FORWARD_HOLD = 21
WALK_FORWARD_PERIODS = 12

FOOTER = "Educational analysis, not financial advice."
