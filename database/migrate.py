"""Alembic entrypoint that works from an installed package (no alembic.ini needed).

    python -m database.migrate upgrade head
    python -m database.migrate downgrade base
    python -m database.migrate revision --autogenerate -m "add x"
    python -m database.migrate check        # fail if models and migrations differ

The database URL comes from ``DEVAGENT_DATABASE_URL``.
"""

import logging
import os
import sys
from pathlib import Path

from alembic.config import CommandLine, Config

DEFAULT_URL = "postgresql+asyncpg://devagent:devagent@localhost:5432/devagent"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def build_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    url = database_url or os.environ.get("DEVAGENT_DATABASE_URL", DEFAULT_URL)
    # ConfigParser interpolation treats "%" specially; escape it in passwords.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    cli = CommandLine(prog="python -m database.migrate")
    options = cli.parser.parse_args(argv)
    if not hasattr(options, "cmd"):
        cli.parser.error("too few arguments")
    cli.run_cmd(build_config(), options)
    return 0


if __name__ == "__main__":
    sys.exit(main())
