"""Full mock end-to-end run through the REAL Core HTTP API (CI runs this).

    make smoke                                               # starts its own Core
    python scripts/smoke_e2e.py --base-url http://localhost:8080   # an already-running Core

Part 1 -- Relay path, public API: create case -> consents -> visit -> transcript ->
evidence -> claim -> analyze -> call blocked -> answers (signed webhook + REST) ->
dispute -> approval -> call -> SSN handoff -> human resolves -> RESOLVED.
Part 2 -- ASI:One path: the orchestrator conversation via LOCAL_DIRECT over HTTP
(typed Visit/Claim/Dispute logic calling /internal/tools/*).

The self-hosted Core runs Alembic migrations on a temp SQLite DB, all modes mock.
"""

import argparse
import contextlib
import dataclasses
import gc
import logging
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config

from app.config import Settings
from app.demo_support import create_demo_visit, isolated_settings, run_demo_flow, service_headers
from app.main import create_app
from claimshield_contracts.enums import ServiceName

CORE_DIR = Path(__file__).resolve().parents[1] / "core"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _migrate(url: str) -> None:
    config = Config(str(CORE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_DIR / "app" / "migrations"))
    config.attributes["configure_logging"] = False
    config.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()
    command.upgrade(config, "head")


@contextlib.contextmanager
def local_core(db_path: Path) -> Iterator[tuple[str, Settings]]:
    """Core on a free port: migrated SQLite, every integration in mock mode."""
    settings = isolated_settings(db_path, log_level="WARNING")
    _migrate(settings.resolved_database_url)
    port = _free_port()
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", settings
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        app.state.container.engine.dispose()  # release the SQLite file on Windows
        gc.collect()  # drop the migration engine reference too


def chat_leg(base_url: str, settings: Settings) -> str:
    """The ASI:One conversation via LOCAL_DIRECT against the same Core."""
    from fetch.agents.local_direct import DEMO_CONVERSATION, LocalDirectRunner
    from fetch.agents.settings import AgentSettings

    create_demo_visit(
        httpx.Client(base_url=base_url, timeout=60),
        service_headers(settings, ServiceName.RELAY),
        data_consent=False,
    )
    agent_settings = dataclasses.replace(
        AgentSettings.from_env(),
        core_base_url=base_url,
        fetch_token=settings.service_tokens()[ServiceName.FETCH],
    )
    runner = LocalDirectRunner(settings=agent_settings)
    replies: list[str] = []
    for text in DEMO_CONVERSATION:
        replies.extend(runner.send(text))
    if "Resolved" not in replies[-1]:
        raise AssertionError(f"chat did not resolve: {replies[-3:]}")
    return replies[-1]


def run(base_url: str, settings: Settings, *, chat: bool) -> None:
    print(f"Amyna smoke test (mock mode) against {base_url}")
    print("Part 1: Relay path through the public API")
    summary = run_demo_flow(
        httpx.Client(base_url=base_url, timeout=60), settings, log=lambda m: print(f"  ok {m}")
    )
    print(f"  -> {summary}")
    if chat:
        print("Part 2: ASI:One conversation via LOCAL_DIRECT agents over HTTP")
        print(f"  ok {chat_leg(base_url, settings)}")
    print("SMOKE PASSED")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--base-url", help="use a running Core (mock modes) instead of starting one"
    )
    parser.add_argument("--skip-chat", action="store_true", help="skip the ASI:One chat leg")
    args = parser.parse_args()
    logging.disable(logging.INFO)
    try:
        if args.base_url:
            run(args.base_url.rstrip("/"), Settings(), chat=not args.skip_chat)
        else:
            with (
                tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp,
                local_core(Path(tmp) / "smoke.db") as (url, s),
            ):
                run(url, s, chat=not args.skip_chat)
    except Exception as exc:  # report clearly and fail the build
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
