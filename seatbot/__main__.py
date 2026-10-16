import logging
import sys

from .client import LoginFailed, PnuLibraryClient
from .config import Config, ConfigError
from .runner import check, run


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        cfg = Config.from_env()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    client = PnuLibraryClient()
    try:
        if cfg.mode == "check":
            return 0 if check(cfg, client) else 1
        return 0 if run(cfg, client) else 1
    except LoginFailed as e:
        print(f"로그인 실패: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
