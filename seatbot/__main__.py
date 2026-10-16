import logging
import sys

from .client import PnuPlaceClient
from .config import Config, ConfigError
from .runner import run


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        cfg = Config.from_env()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    return 0 if run(cfg, PnuPlaceClient()) else 1


if __name__ == "__main__":
    sys.exit(main())
