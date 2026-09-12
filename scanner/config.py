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
BATCH_SIZE = 100
BENCHMARK = "SPY"

CATALYST_ENRICH_LIMIT = 60
CATALYST_LOOKBACK_HOURS = 72
CATALYST_LOOKAHEAD_DAYS = 7
CATALYST_STRONG_SCORE = 45
CATALYST_ACTIVE_SCORE = 30

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
