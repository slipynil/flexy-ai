import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.farm import register_accounts_no_email

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("KrakenBot")


def main():
    parser = argparse.ArgumentParser(
        description="Регистрация аккаунтов flexy-ai.com без подтверждения почты (ADR-003)"
    )
    parser.add_argument("--counts", required=True, help="количество аккаунтов для регистрации", type=int)
    args = parser.parse_args()

    registered = register_accounts_no_email(args.counts)
    logger.info(f"Готово: зарегистрировано {registered} из {args.counts}")


if __name__ == "__main__":
    main()
