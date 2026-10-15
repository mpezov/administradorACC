from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT / ".env")

from app import create_app
from waitress import serve

app = create_app()

if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=8003, threads=8)
