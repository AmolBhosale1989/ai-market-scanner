from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
UNIVERSE_FILE = DATA_DIR / "universe.csv"
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# Tradable-universe gate.
MIN_PRICE = 5.0
MIN_AVG_SHARE_VOLUME = 1_000_000
MIN_AVG_DOLLAR_VOLUME = 20_000_000
MIN_MEDIAN_DOLLAR_VOLUME = 25_000_000
MIN_ADR20_PCT = 2.0
MIN_ATR_PCT = 2.0
MAX_ATR_PCT = 8.0
MAX_BID_ASK_SPREAD_PCT = 0.50
PREFILTER_PERIOD = "3mo"
PREFILTER_MIN_BARS = 40
PREFILTER_AVG_WINDOW = 20

MIN_HISTORY_DAYS = 220
MIN_RUNWAY_PCT = 5.0
MIN_EFFECTIVE_RR = 2.5

TOP_N = 20
LEADER_WATCHLIST_LIMIT = 20
CORE_LEADER_TICKERS = (
    "AAPL", "AMD", "AMZN", "AVGO", "AXTI",
    "COIN", "CRDO", "GOOGL", "HOOD", "INTC",
    "IOVA", "META", "MSFT", "MSTR", "MU",
    "NFLX", "NVDA", "PLTR", "QCOM", "TSLA",
)
BATCH_SIZE = 75
BENCHMARK = "SPY"

# Production warehouse contract. 5,341 is the audited master-universe baseline
# from the last complete V3 run. Exchange listings can change, so production
# records drift instead of silently substituting a smaller catalogue.
MASTER_UNIVERSE_BASELINE = 5_341
MASTER_UNIVERSE_MINIMUM = 5_000
MASTER_UNIVERSE_MAX_DRIFT = 250
SECTOR_ETFS = (
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
    "XLP", "XLRE", "XLU", "XLV", "XLY",
)
THEME_ETFS = (
    "ARKG", "BOTZ", "COPX", "FINX", "GDX", "HACK", "ICLN",
    "ITA", "KRE", "MSOS", "OIH", "PAVE", "SKYY", "SMH",
    "URA", "WGMI", "XBI", "XHB", "XLE",
)
CRITICAL_MARKET_SYMBOLS = tuple(dict.fromkeys((BENCHMARK,) + SECTOR_ETFS + THEME_ETFS))
ROTATION_CONSTITUENTS = (
    "AEM","AI","ALNY","AMAT","AMD","AMZN","APLD","ARBK","ARM","ASML","AU","AVAV","AVGO",
    "BE","BEAM","BITF","BMRN","BTDR","CAN","CCJ","CGNX","CHKP","CIFR","CLSK","COP","CORZ",
    "CRM","CRSP","CRWD","CVX","CYBR","DDOG","DNN","DVN","ENPH","EOG","ESTC","FANG","FNV",
    "FSLR","FTNT","GD","GEN","GOLD","GTLB","HII","HUT","INTC","IOVA","IREN","ISRG","IONS",
    "KGC","KLAC","KTOS","LEU","LHX","LMT","LRCX","MARA","MDB","MPWR","MRNA","MRVL","MSFT",
    "MU","NBIX","NEM","NET","NOC","NOW","NTLA","NVDA","NXE","OKLO","OKTA","ON","ORCL","OXY",
    "PANW","PATH","PLTR","PLUG","QCOM","QLYS","REGN","RIOT","RKLB","RBRK","ROK","RTX","RUN",
    "S","SEDG","SMR","SNOW","SYM","TENB","TER","TSM","UEC","UUUU","VLO","VRNS","VRTX","WPM",
    "WULF","XOM","ZS",
)
INGESTION_CRITICAL_SYMBOLS = tuple(dict.fromkeys(CRITICAL_MARKET_SYMBOLS + ROTATION_CONSTITUENTS))

# Tiered coverage gates: broad discovery tolerates delisted/provider-unavailable
# names, while benchmarks/ETFs and selected live names are strict.
MASTER_DAILY_MIN_COVERAGE = 0.75
CRITICAL_DAILY_MIN_COVERAGE = 1.00
CRITICAL_INTRADAY_MIN_COVERAGE = 1.00
LIVE_INTRADAY_MIN_COVERAGE = 0.95

# Data-integrity controls.
MIN_DATA_COVERAGE = 0.75
MIN_ANALYZABLE_COVERAGE = 0.80
BATCH_RETRIES = 3
RETRY_CHUNK_SIZE = 20
RETRY_BACKOFF_SECONDS = 2.0

# Catalyst/news enrichment.
CATALYST_ENRICH_LIMIT = 60
CATALYST_LOOKBACK_HOURS = 72
CATALYST_LOOKAHEAD_DAYS = 7
CATALYST_STRONG_SCORE = 45
CATALYST_ACTIVE_SCORE = 30
CATALYST_CONTEXT_MAX_HOURS = 168

# Event-first pre-catalyst discovery.
EVENT_SCAN_LIMIT = 1200
EVENT_LOOKAHEAD_DAYS = 7
EVENT_MAX_WORKERS = 12
EVENT_NEWS_SCAN_LIMIT = 150
EARNINGS_INTEL_LIMIT = 25
EARNINGS_HISTORY_QUARTERS = 8

# Theme/sector momentum.
THEME_PROFILE_LIMIT = 80
THEME_BONUS_MAX = 6.0

# Live confirmation.
LIVE_ENRICH_LIMIT = 20
LIVE_INTERVAL = "5m"
LIVE_PERIOD = "5d"
LIVE_MIN_INTRADAY_RVOL = 1.20
OPENING_RANGE_MINUTES = 30

# Backtesting defaults.
BACKTEST_PERIOD = "3y"
BACKTEST_HORIZON_DAYS = 7
BACKTEST_MAX_TICKERS = 100
BACKTEST_SIGNAL_STRIDE = 5

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
