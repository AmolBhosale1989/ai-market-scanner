from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
UNIVERSE_FILE = DATA_DIR / "universe.csv"
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

MIN_PRICE = 3.0
MIN_AVG_DOLLAR_VOLUME = 20_000_000
MIN_HISTORY_DAYS = 220
MIN_RUNWAY_PCT = 5.0

TOP_N = 20
BATCH_SIZE = 75
BENCHMARK = "SPY"

# Data-integrity controls.
MIN_DATA_COVERAGE = 0.75
MIN_ANALYZABLE_COVERAGE = 0.20
BATCH_RETRIES = 3
RETRY_CHUNK_SIZE = 20
RETRY_BACKOFF_SECONDS = 2.0

# Catalyst/news enrichment.
CATALYST_ENRICH_LIMIT = 60
CATALYST_LOOKBACK_HOURS = 72
CATALYST_LOOKAHEAD_DAYS = 7
CATALYST_STRONG_SCORE = 45
CATALYST_ACTIVE_SCORE = 30

# Theme/sector momentum. Theme strength is an additive ranking bonus, not a
# hard requirement for a trade.
THEME_PROFILE_LIMIT = 80
THEME_BONUS_MAX = 6.0

# Live confirmation.
LIVE_ENRICH_LIMIT = 20
LIVE_INTERVAL = "5m"
LIVE_PERIOD = "5d"
LIVE_MIN_INTRADAY_RVOL = 1.20
OPENING_RANGE_MINUTES = 30

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
