"""Pure construction of snapshot-bound World affordances."""

from __future__ import annotations

import json
from hashlib import sha256

from agent_runtime.model import StrictModel
from agent_runtime.scenario import ObjectSeed
from agent_runtime.world.contracts import (
    Affordance,
    CharacterTarget,
    DeliveryChannel,
    Identifier,
    ObjectTarget,
    ProposalKind,
    WorldRef,
    WorldVersion,
)
from agent_runtime.world.state import ObjectState


class WorldAffordanceResolver(StrictModel):
    """Issue deterministic choices for one Agent and one committed snapshot."""

    world_ref: WorldRef
    world_version: WorldVersion
    agent_id: Identifier

    def simple_affordance(self, kind: ProposalKind) -> Affordance:
        """Issue one snapshot-bound choice for act or wait."""

        if kind not in {ProposalKind.ACT, ProposalKind.WAIT}:
            raise ValueError("simple affordance kind must be act or wait")
        return Affordance(
            affordance_id=self._id(kind=kind),
            kind=kind,
        )

    def object_affordances(
        self,
        *,
        object_state: ObjectState,
        object_seed: ObjectSeed,
    ) -> tuple[Affordance, ...]:
        """Return the registered operations currently enabled for one object."""

        if object_state.world_ref != self.world_ref:
            raise ValueError("object state belongs to a different WorldRef")
        if object_state.object_id != object_seed.id:
            raise ValueError("object state does not match the Scenario object")

        target = ObjectTarget(id=object_state.object_id)
        return tuple(
            Affordance(
                affordance_id=self._id(
                    kind=ProposalKind.INTERACT,
                    target=target,
                    operation_id=operation.operation_id,
                    current_state=object_state.state,
                ),
                kind=ProposalKind.INTERACT,
                target=target,
                operation_id=operation.operation_id,
            )
            for operation in sorted(object_seed.operations, key=lambda item: item.operation_id)
            if operation.from_state == object_state.state
        )

    def utter_affordance(
        self,
        *,
        target: CharacterTarget,
        delivery_channel: DeliveryChannel,
    ) -> Affordance:
        """Issue one direct or whisper speech choice."""

        return Affordance(
            affordance_id=self._id(
                kind=ProposalKind.UTTER,
                target=target,
                delivery_channel=delivery_channel,
            ),
            kind=ProposalKind.UTTER,
            target=target,
            delivery_channel=delivery_channel,
        )

    def response_affordance(
        self,
        *,
        target: CharacterTarget,
        delivery_channel: DeliveryChannel,
        request_entry_id: Identifier,
    ) -> Affordance:
        """Issue one response choice bound to its pending request Entry."""

        return Affordance(
            affordance_id=self._id(
                kind=ProposalKind.RESPOND,
                target=target,
                delivery_channel=delivery_channel,
                request_entry_id=request_entry_id,
            ),
            kind=ProposalKind.RESPOND,
            target=target,
            delivery_channel=delivery_channel,
            request_entry_id=request_entry_id,
        )

    def _id(
        self,
        *,
        kind: ProposalKind,
        target: CharacterTarget | ObjectTarget | None = None,
        operation_id: str | None = None,
        delivery_channel: DeliveryChannel | None = None,
        request_entry_id: str | None = None,
        current_state: str | None = None,
    ) -> str:
        identity = (
            self.world_ref.project_id,
            self.world_ref.world_id,
            self.world_version,
            self.agent_id,
            kind.value,
            target.kind if target is not None else None,
            target.id if target is not None else None,
            operation_id,
            delivery_channel.value if delivery_channel is not None else None,
            request_entry_id,
            current_state,
        )
        canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        return f"affordance-{sha256(canonical.encode()).hexdigest()}"
