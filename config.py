# from dotenv import load_dotenv
# import os

# load_dotenv()

# api_url = os.getenv("API_URL")
# api_key = os.getenv("API_KEY")

from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_PATH)

API_URL = os.getenv("API_URL")
API_KEY = os.getenv("API_KEY")