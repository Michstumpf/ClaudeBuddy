import argparse
import logging

import uvicorn

from .app import create_app
from .config import TOKEN_FILE, Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Buddy hub")
    parser.add_argument("--host", help="overrides [hub] listen (default 127.0.0.1); 0.0.0.0 needs allowed_networks")
    parser.add_argument("--port", type=int, help="overrides [hub] port (default 8765)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    host, port = args.host or settings.listen, args.port or settings.port
    print(f"Claude Buddy hub on http://{host}:{port}, serving {', '.join(settings.allowed_networks)}")
    print(f"Simulator: http://localhost:{port}/?token=<token>   (token in {TOKEN_FILE} or $BUDDY_TOKEN)")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
