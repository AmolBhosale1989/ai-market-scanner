from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
UNIVERSE_FILE = BASE_DIR / "data" / "universe.csv"
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

MIN_PRICE = 3.0
MIN_AVG_DOLLAR_VOLUME = 20_000_000
TOP_N = 10
