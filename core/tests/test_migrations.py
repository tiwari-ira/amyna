"""The Alembic migration creates exactly the tables the models define."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db import Base

CORE_DIR = Path(__file__).resolve().parents[1]


def test_upgrade_head_matches_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config(str(CORE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_DIR / "app" / "migrations"))
    config.attributes["configure_logging"] = False
    config.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(config, "head")
    tables = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        columns = {c["name"] for c in inspect(create_engine(url)).get_columns(name)}
        assert columns == set(table.columns.keys()), name
