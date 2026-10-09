"""Print agent addresses derived from the *_SEED env vars (never prints seeds).

make agent-addresses   ->  paste the *_ADDRESS lines into .env
"""

from uagents_core.identity import Identity

from .settings import MissingSeedError, require_seed

SEEDS = {
    "ORCHESTRATOR": "ORCHESTRATOR_SEED",
    "VISIT_AGENT": "VISIT_AGENT_SEED",
    "CLAIM_AGENT": "CLAIM_AGENT_SEED",
    "DISPUTE_AGENT": "DISPUTE_AGENT_SEED",
}


def main() -> None:
    for name, variable in SEEDS.items():
        try:
            address = Identity.from_seed(require_seed(variable), 0).address
        except MissingSeedError:
            print(f"# {variable} is not set")
            continue
        print(f"{name}_ADDRESS={address}")


if __name__ == "__main__":
    main()
