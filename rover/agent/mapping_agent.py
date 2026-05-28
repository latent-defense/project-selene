import sys
from mapping.io import OUTPUT_PATH, write_map_artifact


"""
Entrypoint
"""


def main() -> None:
    from mapping.state_machine import MappingStateMachine

    machine = MappingStateMachine(write_artifact_fn=write_map_artifact)
    artifact = machine.run()
    print(
        f"[mapping] Wrote scaffold map artifact to {OUTPUT_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
