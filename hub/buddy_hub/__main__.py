import argparse
import logging

import uvicorn

from .app import create_app
from .config import TOKEN_FILE, Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Buddy hub")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 so the ESP32 on the LAN can connect")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    print(f"Claude Buddy hub on http://{args.host}:{args.port}")
    print(f"Simulator: http://localhost:{args.port}/?token=<token>   (token in {TOKEN_FILE} or $BUDDY_TOKEN)")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
