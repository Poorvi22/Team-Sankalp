"""Start BizPilot: seeds the demo database on first run, then serves http://127.0.0.1:8000"""
import argparse

import uvicorn

from server import db, seed

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reseed", action="store_true", help="regenerate the synthetic dataset")
    a = ap.parse_args()
    if a.reseed or not db.DB_PATH.exists():
        seed.seed()
    uvicorn.run("server.main:app", host=a.host, port=a.port, log_level="warning")
