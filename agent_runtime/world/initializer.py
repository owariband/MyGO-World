"""Public World materialization inside a caller-owned bootstrap transaction."""

from sqlalchemy.orm import Session

from agent_runtime.world.state import PublicWorldState
from agent_runtime.world.storage import WorldStore


def initialize_public_world(session: Session, state: PublicWorldState) -> None:
    """Stage all public Genesis rows without beginning or committing a transaction."""

    WorldStore(state.world.world_ref).insert_initial(session, state)
