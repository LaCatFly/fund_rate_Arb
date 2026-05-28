"""CLI package."""
from pathlib import Path
from dotenv import load_dotenv

# Load .env from repo root or package directory
for candidate in [Path(__file__).resolve().parent.parent.parent / ".env",
                  Path(__file__).resolve().parent / ".env"]:
    if candidate.exists():
        load_dotenv(candidate)
        break

from fund_rate_arb.cli.main import cli
