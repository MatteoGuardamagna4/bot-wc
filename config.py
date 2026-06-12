"""Central configuration. Loads credentials from .env."""
import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
# Sofascore is blocked (403) on many IPs — disabled by default.
# Set SOFASCORE_ENABLED=true in .env to re-enable it as ESPN backup.
SOFASCORE_ENABLED = os.getenv("SOFASCORE_ENABLED", "false").strip().lower() == "true"

OPENAI_MODEL = "gpt-4o"

# HTTP settings for scraping
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# FIFA-style codes -> full team names (extend as needed)
TEAM_CODES = {
    "ARG": "Argentina", "FRA": "France", "BRA": "Brazil", "GER": "Germany",
    "ESP": "Spain", "ENG": "England", "POR": "Portugal", "NED": "Netherlands",
    "ITA": "Italy", "BEL": "Belgium", "CRO": "Croatia", "URU": "Uruguay",
    "MEX": "Mexico", "USA": "USA", "CAN": "Canada", "JPN": "Japan",
    "KOR": "South Korea", "AUS": "Australia", "MAR": "Morocco",
    "SEN": "Senegal", "GHA": "Ghana", "CMR": "Cameroon", "NGA": "Nigeria",
    "ECU": "Ecuador", "COL": "Colombia", "CHI": "Chile", "PER": "Peru",
    "SUI": "Switzerland", "POL": "Poland", "DEN": "Denmark", "SWE": "Sweden",
    "NOR": "Norway", "SRB": "Serbia", "WAL": "Wales", "SCO": "Scotland",
    "AUT": "Austria", "CZE": "Czech Republic", "UKR": "Ukraine",
    "TUN": "Tunisia", "ALG": "Algeria", "EGY": "Egypt", "CIV": "Ivory Coast",
    "BIH": "Bosnia and Herzegovina", "SVK": "Slovakia", "SVN": "Slovenia",
    "TUR": "Turkey", "GRE": "Greece", "HUN": "Hungary", "ROU": "Romania",
    "JOR": "Jordan", "UZB": "Uzbekistan", "IRQ": "Iraq", "RSA": "South Africa",
    "CPV": "Cape Verde", "HAI": "Haiti", "CUW": "Curacao",
    "KSA": "Saudi Arabia", "QAT": "Qatar", "IRN": "Iran", "CRC": "Costa Rica",
    "PAN": "Panama", "PAR": "Paraguay", "VEN": "Venezuela", "NZL": "New Zealand",
}


def resolve_team(name: str) -> str:
    """Resolve a team code or name to a full team name."""
    return TEAM_CODES.get(name.strip().upper(), name.strip().title())
