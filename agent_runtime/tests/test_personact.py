"""Contract tests for restricted NPC compilation and action proposals."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
    compile_manifest,
)
from agent_runtime.agent.personact.errors import (
    ManifestCompileError,
    ManifestDecodeError,
    ProposalValidationError,
)
from agent_runtime.agent.personact.manifest import (
    CapabilityRequest,
    Manifest,
    NPCDefinition,
    ToolRequest,
    load_manifest,
)
from agent_runtime.agent.personact.proposal import ProposalDraft, build_action_proposal
from agent_runtime.agent.personact.state import PlanDisposition
from agent_runtime.agent.skill import RuntimeSkill, RuntimeSkillCatalog, load_runtime_skill
from agent_runtime.scenario import ObjectOperationSeed, ObjectSeed
from agent_runtime.world.affordances import WorldAffordanceResolver
from agent_runtime.world.contracts import (
    ActAction,
    ActionProposal,
    Affordance,
    AgentView,
    CharacterTarget,
    CommitPosition,
    DeliveryChannel,
    InteractAction,
    NoOpAction,
    ObjectTarget,
    ProposalKind,
    RespondAction,
    UtterAction,
    WorldRef,
)
from agent_runtime.world.state import ObjectState

WORLD_REF = WorldRef(project_id="coffee-golden", world_id="save-001")
FIXTURE_PATH = Path(__file__).parents[1] / "testdata" / "npc_diy" / "agents.json"
SKILLS_PATH = Path(__file__).parents[2] / "content" / "skills"


def test_compile_is_stable_and_derives_authority() -> None:
    manifest = load_manifest(FIXTURE_PATH)

    first = compile_manifest(manifest, _catalog())
    second = compile_manifest(manifest, _catalog())

    assert first[0].digest == second[0].digest
    assert first[0].memory_scope == "project/coffee-golden/persona/anon"
    assert first[0].character_skill.skill_id == "mygo.character.anon"
    assert first[0].character_skill.version == "3.0.0"
    assert first[0].character_skill.content_hash == _catalog().skills[0].content_hash
    assert first[0].seeds[0].provenance.startswith(f"manifest:{first[0].digest}#")


def test_character_skill_content_hash_partitions_compiled_digest(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURE_PATH)
    catalog = _catalog()
    original = compile_manifest(manifest, catalog)[0]
    anon = catalog.skills[0]
    changed_path = tmp_path / "anon.md"
    changed_path.write_text(
        anon.source_text.replace(anon.body, f"{anon.body} changed"),
        encoding="utf-8",
    )
    changed = load_runtime_skill(changed_path)
    changed_catalog = Catalog(
        tools=catalog.tools,
        prompts=catalog.prompts,
        skills=(changed, *catalog.skills[1:]),
    )

    changed_spec = compile_manifest(manifest, changed_catalog)[0]

    assert changed_spec.character_skill.content_hash == changed.content_hash
    assert changed_spec.digest != original.digest


def test_manifest_rejects_unknown_authority_fields(tmp_path: Path) -> None:
    manifest_path = tmp_path / "agents.json"
    manifest_path.write_text(
        '{"formatVersion":2,"projectId":"p","provider":"creator-model","agents":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ManifestDecodeError, match="Extra inputs are not permitted"):
        load_manifest(manifest_path)


def test_manifest_is_strict_and_does_not_coerce_types(tmp_path: Path) -> None:
    raw = FIXTURE_PATH.read_text(encoding="utf-8").replace(
        '"maxContextRounds": 1',
        '"maxContextRounds": "1"',
        1,
    )
    manifest_path = tmp_path / "agents.json"
    manifest_path.write_text(raw, encoding="utf-8")

    with pytest.raises(ManifestDecodeError, match="valid integer"):
        load_manifest(manifest_path)


def test_manifest_v1_is_rejected_after_character_skill_became_required(
    tmp_path: Path,
) -> None:
    raw = FIXTURE_PATH.read_text(encoding="utf-8").replace(
        '"formatVersion": 2',
        '"formatVersion": 1',
        1,
    )
    manifest_path = tmp_path / "agents.json"
    manifest_path.write_text(raw, encoding="utf-8")

    with pytest.raises(ManifestDecodeError, match="Input should be 2"):
        load_manifest(manifest_path)


def test_manifest_rejects_unknown_proposal_kind() -> None:
    raw = FIXTURE_PATH.read_text(encoding="utf-8").replace(
        '"utter", "respond", "wait", "no_op"',
        '"commit_world"',
        1,
    )

    with pytest.raises(ValidationError, match="commit_world"):
        Manifest.model_validate_json(raw, strict=True, extra="forbid")


def test_project_identity_partitions_digest_and_memory_scope() -> None:
    first = load_manifest(FIXTURE_PATH)
    second = Manifest(
        format_version=first.format_version,
        project_id="another-project",
        agents=first.agents,
    )

    first_spec = compile_manifest(first, _catalog())[0]
    second_spec = compile_manifest(second, _catalog())[0]

    assert first_spec.digest != second_spec.digest
    assert first_spec.memory_scope != second_spec.memory_scope
    assert second_spec.memory_scope == "project/another-project/persona/anon"


def test_frozen_models_reject_mutation_and_strict_revalidation() -> None:
    view = _view(affordances=())

    with pytest.raises(ValidationError, match="Input should be a valid integer"):
        AgentView.model_validate(
            {
                **view.model_dump(by_alias=False),
                "based_on_world_version": "7",
            },
            strict=True,
        )
    assert view.model_config.get("frozen") is True


def test_compile_rejects_duplicate_catalog_ids() -> None:
    duplicate = ToolDefinition(
        id="visible_location.query",
        version="2",
        mode=ToolMode.QUERY,
    )
    catalog = Catalog(
        tools=(*_catalog().tools, duplicate),
        prompts=_catalog().prompts,
    )

    with pytest.raises(ManifestCompileError, match="catalog tool ids must be unique"):
        compile_manifest(load_manifest(FIXTURE_PATH), catalog)


def test_compile_rejects_duplicate_memory_seed_ids() -> None:
    raw = FIXTURE_PATH.read_text(encoding="utf-8").replace(
        '"seeds": [{',
        '"seeds": [{"id":"knows-soyo","type":"background","content":"duplicate","tags":[]},{',
        1,
    )
    manifest = Manifest.model_validate_json(raw, strict=True, extra="forbid")

    with pytest.raises(ManifestCompileError, match="memory seed ids must be unique"):
        compile_manifest(manifest, _catalog())


def test_compile_rejects_mutating_tool() -> None:
    manifest = load_manifest(FIXTURE_PATH)
    capabilities = CapabilityRequest(
        proposal_kinds=(ProposalKind.UTTER,),
        tools=(ToolRequest(id="world.commit", max_calls_per_run=1),),
    )
    modified = _replace_first_agent(manifest, capabilities=capabilities)

    with pytest.raises(ManifestCompileError, match="non-read-only tool"):
        compile_manifest(modified, _catalog())


def test_compile_rejects_unknown_prompt_profile() -> None:
    manifest = load_manifest(FIXTURE_PATH)
    modified = _replace_first_agent(manifest, prompt_profile="creator.system_prompt")

    with pytest.raises(ManifestCompileError, match="unknown prompt profile"):
        compile_manifest(modified, _catalog())


def test_compile_rejects_unknown_or_wrong_kind_character_skill() -> None:
    manifest = load_manifest(FIXTURE_PATH)
    without_skills = Catalog(
        tools=_catalog().tools,
        prompts=_catalog().prompts,
    )
    with pytest.raises(ManifestCompileError, match="unknown Character Skill"):
        compile_manifest(manifest, without_skills)

    wrong_kind = Catalog(
        tools=_catalog().tools,
        prompts=_catalog().prompts,
        skills=(
            _skill("mygo.character.anon", "3.0.0", agent_kind="director"),
            _skill("mygo.character.soyo", "2.0.0"),
        ),
    )
    with pytest.raises(ManifestCompileError, match="non-character Skill"):
        compile_manifest(manifest, wrong_kind)


def test_build_action_proposal_has_fixed_envelope_and_spec_actor() -> None:
    target = CharacterTarget(id="soyo")
    affordance = Affordance(
        affordance_id="utter-soyo-direct",
        kind=ProposalKind.UTTER,
        target=target,
        delivery_channel=DeliveryChannel.DIRECT,
    )
    proposal = build_action_proposal(
        world_ref=WORLD_REF,
        spec=_anon_spec(),
        view=_view(affordances=(affordance,)),
        proposal_id="proposal-1",
        draft=ProposalDraft(
            action=UtterAction(
                affordance_id=affordance.affordance_id,
                target=target,
                content="轮到我们了，要这个吗？",
                expects_response=True,
            ),
            evidence_ids=("queue-ready",),
        ),
    )

    assert isinstance(proposal, ActionProposal)
    assert json.loads(proposal.model_dump_json(by_alias=True)) == {
        "worldRef": {"projectId": "coffee-golden", "worldId": "save-001"},
        "proposalId": "proposal-1",
        "agentId": "anon",
        "eventSessionId": "cafe",
        "basedOnWorldVersion": 7,
        "basedOnControlEpoch": 1,
        "basedOnDecisionSeq": 0,
        "action": {
            "kind": "utter",
            "affordanceId": "utter-soyo-direct",
            "target": {"kind": "character", "id": "soyo"},
            "content": "轮到我们了，要这个吗？",
            "expectsResponse": True,
        },
        "evidenceIds": ["queue-ready"],
    }


def test_proposal_draft_rejects_actor_override() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProposalDraft.model_validate(
            {
                "agentId": "soyo",
                "action": {
                    "kind": "no_op",
                    "nextWakeup": "event_change",
                },
            },
            strict=True,
        )


def test_proposal_draft_keeps_private_plan_disposition_out_of_world_proposal() -> None:
    draft = ProposalDraft(
        action=NoOpAction(next_wakeup="event_change"),
        plan_disposition=PlanDisposition.CANCEL,
    )
    proposal = build_action_proposal(
        world_ref=WORLD_REF,
        spec=_anon_spec(),
        view=_view(affordances=()),
        proposal_id="proposal-1",
        draft=draft,
    )

    assert draft.plan_disposition is PlanDisposition.CANCEL
    assert "planDisposition" not in proposal.model_dump_json(by_alias=True)


def test_world_affordances_are_stable_and_bound_to_snapshot_state() -> None:
    seed = ObjectSeed(
        id="metronome",
        name="Metronome",
        kind="instrument",
        description="A rehearsal metronome.",
        location_id="cafe",
        state="stopped",
        operations=(
            ObjectOperationSeed(
                operation_id="start",
                from_state="stopped",
                to_state="running",
                result_text="The metronome starts.",
            ),
            ObjectOperationSeed(
                operation_id="stop",
                from_state="running",
                to_state="stopped",
                result_text="The metronome stops.",
            ),
        ),
    )
    state = ObjectState(
        world_ref=WORLD_REF,
        object_id="metronome",
        name="Metronome",
        kind="instrument",
        description="A rehearsal metronome.",
        location_id="cafe",
        state="stopped",
    )
    resolver = WorldAffordanceResolver(
        world_ref=WORLD_REF,
        world_version=7,
        agent_id="anon",
    )

    first = resolver.object_affordances(object_state=state, object_seed=seed)
    second = resolver.object_affordances(object_state=state, object_seed=seed)

    assert first == second
    assert tuple(item.operation_id for item in first) == ("start",)
    assert first[0].affordance_id.startswith("affordance-")
    next_version = WorldAffordanceResolver(
        world_ref=WORLD_REF,
        world_version=8,
        agent_id="anon",
    ).object_affordances(object_state=state, object_seed=seed)
    assert next_version[0].affordance_id != first[0].affordance_id


def test_speech_affordances_bind_channel_and_response_source() -> None:
    resolver = WorldAffordanceResolver(
        world_ref=WORLD_REF,
        world_version=7,
        agent_id="anon",
    )
    target = CharacterTarget(id="soyo")

    direct = resolver.utter_affordance(
        target=target,
        delivery_channel=DeliveryChannel.DIRECT,
    )
    whisper = resolver.utter_affordance(
        target=target,
        delivery_channel=DeliveryChannel.WHISPER,
    )
    response = resolver.response_affordance(
        target=target,
        delivery_channel=DeliveryChannel.WHISPER,
        request_entry_id="entry-1",
    )

    assert direct.affordance_id != whisper.affordance_id
    assert response.request_entry_id == "entry-1"
    assert response.affordance_id not in {direct.affordance_id, whisper.affordance_id}
    assert resolver.simple_affordance(ProposalKind.WAIT).kind is ProposalKind.WAIT


def test_agent_view_rejects_future_cursor_and_duplicate_affordance_ids() -> None:
    with pytest.raises(ValidationError, match="newer"):
        AgentView.model_validate(
            {
                **_view(affordances=()).model_dump(by_alias=False),
                "observed_through": CommitPosition(world_version=8),
            },
            strict=True,
        )

    afforded = Affordance(affordance_id="same", kind=ProposalKind.ACT)
    with pytest.raises(ValidationError, match="unique"):
        _view(affordances=(afforded, afforded))


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(CharacterTarget(id="shared-id"), id="character"),
        pytest.param(ObjectTarget(id="shared-id"), id="object"),
    ],
)
def test_build_action_proposal_accepts_character_and_object_interact(
    target: CharacterTarget | ObjectTarget,
) -> None:
    affordance = Affordance(
        affordance_id="interact-shared-id",
        kind=ProposalKind.INTERACT,
        target=target,
        operation_id="use" if isinstance(target, ObjectTarget) else None,
    )
    proposal = build_action_proposal(
        world_ref=WORLD_REF,
        spec=_anon_spec(),
        view=_view(affordances=(affordance,)),
        proposal_id="proposal-1",
        draft=ProposalDraft(
            action=InteractAction(
                affordance_id=affordance.affordance_id,
                target=target,
                description="interact with target",
            )
        ),
    )

    assert isinstance(proposal.action, InteractAction)
    assert proposal.action.target == target


@pytest.mark.parametrize(
    ("afforded_target", "draft_target"),
    [
        pytest.param(
            CharacterTarget(id="shared-id"),
            ObjectTarget(id="shared-id"),
            id="character-affordance-object-draft",
        ),
        pytest.param(
            ObjectTarget(id="shared-id"),
            CharacterTarget(id="shared-id"),
            id="object-affordance-character-draft",
        ),
    ],
)
def test_build_action_proposal_does_not_confuse_target_kind_with_same_id(
    afforded_target: CharacterTarget | ObjectTarget,
    draft_target: CharacterTarget | ObjectTarget,
) -> None:
    affordance = Affordance(
        affordance_id="interact-shared-id",
        kind=ProposalKind.INTERACT,
        target=afforded_target,
        operation_id="use" if isinstance(afforded_target, ObjectTarget) else None,
    )
    view = _view(affordances=(affordance,))
    draft = ProposalDraft(
        action=InteractAction(
            affordance_id=affordance.affordance_id,
            target=draft_target,
            description="interact with target",
        )
    )

    with pytest.raises(ProposalValidationError, match="not afforded"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=view,
            proposal_id="proposal-1",
            draft=draft,
        )


@pytest.mark.parametrize(
    "action",
    [
        pytest.param(
            UtterAction(
                affordance_id="utter-soyo-direct",
                target=CharacterTarget(id="soyo"),
                content="hi",
                expects_response=True,
            ),
            id="utter",
        ),
        pytest.param(
            RespondAction(
                affordance_id="respond-soyo-entry-1",
                target=CharacterTarget(id="soyo"),
                content="hi",
                in_reply_to_entry_id="entry-1",
            ),
            id="respond",
        ),
    ],
)
def test_world_contract_accepts_character_target_for_speech(
    action: UtterAction | RespondAction,
) -> None:
    draft = ProposalDraft(action=action)

    assert draft.action == action
    assert action.target == CharacterTarget(id="soyo")


@pytest.mark.parametrize("kind", [ProposalKind.UTTER, ProposalKind.RESPOND])
def test_world_contract_rejects_object_target_for_speech(kind: ProposalKind) -> None:
    action_details = (
        {"expectsResponse": False}
        if kind is ProposalKind.UTTER
        else {"inReplyToEntryId": "entry-1"}
    )
    with pytest.raises(ValidationError, match="character"):
        ProposalDraft.model_validate(
            {
                "action": {
                    "kind": kind.value,
                    "affordanceId": "speech-affordance",
                    "target": {"kind": "object", "id": "coffee-42"},
                    "content": "hi",
                    **action_details,
                }
            },
            strict=True,
        )


@pytest.mark.parametrize(
    "affordance",
    [
        pytest.param(
            Affordance(
                affordance_id="wrong-target",
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="tomori"),
            ),
            id="wrong-target",
        ),
        pytest.param(
            Affordance(
                affordance_id="wrong-kind",
                kind=ProposalKind.UTTER,
                target=CharacterTarget(id="soyo"),
                delivery_channel=DeliveryChannel.DIRECT,
            ),
            id="wrong-kind",
        ),
    ],
)
def test_build_action_proposal_rejects_wrong_target_or_affordance(
    affordance: Affordance,
) -> None:
    draft = ProposalDraft(
        action=InteractAction(
            affordance_id="wrong-target",
            target=CharacterTarget(id="soyo"),
            description="talk to Soyo",
        )
    )

    with pytest.raises(ProposalValidationError, match="not afforded"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=_view(affordances=(affordance,)),
            proposal_id="proposal-1",
            draft=draft,
        )


def test_build_action_proposal_rejects_forged_affordance_or_response_source() -> None:
    target = CharacterTarget(id="soyo")
    utter_affordance = Affordance(
        affordance_id="utter-soyo-direct",
        kind=ProposalKind.UTTER,
        target=target,
        delivery_channel=DeliveryChannel.DIRECT,
    )
    with pytest.raises(ProposalValidationError, match="not afforded"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=_view(affordances=(utter_affordance,)),
            proposal_id="proposal-1",
            draft=ProposalDraft(
                action=UtterAction(
                    affordance_id="forged",
                    target=target,
                    content="hi",
                    expects_response=False,
                )
            ),
        )

    response_affordance = Affordance(
        affordance_id="respond-soyo-entry-1",
        kind=ProposalKind.RESPOND,
        target=target,
        delivery_channel=DeliveryChannel.DIRECT,
        request_entry_id="entry-1",
    )
    with pytest.raises(ProposalValidationError, match="not afforded"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=_view(affordances=(response_affordance,)),
            proposal_id="proposal-2",
            draft=ProposalDraft(
                action=RespondAction(
                    affordance_id=response_affordance.affordance_id,
                    target=target,
                    content="hi",
                    in_reply_to_entry_id="entry-2",
                )
            ),
        )


def test_build_action_proposal_rejects_kind_not_granted_by_spec() -> None:
    with pytest.raises(ProposalValidationError, match="not granted"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=_view(affordances=(Affordance(affordance_id="act", kind=ProposalKind.ACT),)),
            proposal_id="proposal-1",
            draft=ProposalDraft(action=ActAction(description="look around")),
        )


def test_build_action_proposal_rejects_hidden_evidence() -> None:
    target = CharacterTarget(id="soyo")
    affordance = Affordance(
        affordance_id="utter-soyo-direct",
        kind=ProposalKind.UTTER,
        target=target,
        delivery_channel=DeliveryChannel.DIRECT,
    )
    draft = ProposalDraft(
        action=UtterAction(
            affordance_id=affordance.affordance_id,
            target=target,
            content="hi",
            expects_response=False,
        ),
        evidence_ids=("tomori-private-event",),
    )

    with pytest.raises(ProposalValidationError, match="outside the current view"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=_view(affordances=(affordance,)),
            proposal_id="proposal-1",
            draft=draft,
        )


def test_no_op_needs_no_affordance_and_carries_no_evidence() -> None:
    proposal = build_action_proposal(
        world_ref=WORLD_REF,
        spec=_anon_spec(),
        view=_view(affordances=()),
        proposal_id="proposal-1",
        draft=ProposalDraft(action=NoOpAction(next_wakeup="event_change")),
    )

    assert isinstance(proposal.action, NoOpAction)
    assert proposal.action.next_wakeup == "event_change"
    assert proposal.evidence_ids == ()

    with pytest.raises(ProposalValidationError, match="cannot carry evidence"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=_view(affordances=(), visible_evidence_ids=("queue-ready",)),
            proposal_id="proposal-2",
            draft=ProposalDraft(
                action=NoOpAction(next_wakeup="event_change"),
                evidence_ids=("queue-ready",),
            ),
        )


@pytest.mark.parametrize(
    "foreign_ref",
    [
        WorldRef(project_id="another-project", world_id="save-001"),
        WorldRef(project_id="coffee-golden", world_id="save-002"),
    ],
)
def test_proposal_builder_refuses_foreign_view_even_for_no_op(foreign_ref: WorldRef) -> None:
    view = _view(affordances=())
    foreign_view = AgentView.model_validate(
        {**view.model_dump(by_alias=False), "world_ref": foreign_ref}, strict=True
    )
    with pytest.raises(ProposalValidationError, match="different WorldRef"):
        build_action_proposal(
            world_ref=WORLD_REF,
            spec=_anon_spec(),
            view=foreign_view,
            proposal_id="same-id",
            draft=ProposalDraft(action=NoOpAction(next_wakeup="event_change")),
        )


def test_proposal_builder_refuses_ref_project_mismatching_spec() -> None:
    foreign = WorldRef(project_id="another-project", world_id="save-001")
    view = AgentView.model_validate(
        {**_view(affordances=()).model_dump(by_alias=False), "world_ref": foreign}, strict=True
    )
    with pytest.raises(ProposalValidationError, match="project"):
        build_action_proposal(
            world_ref=foreign,
            spec=_anon_spec(),
            view=view,
            proposal_id="same-id",
            draft=ProposalDraft(action=NoOpAction(next_wakeup="event_change")),
        )


@pytest.mark.parametrize(
    "identity",
    [
        {"worldRef": {"projectId": "p", "worldId": "w"}},
        {"projectId": "p"},
        {"worldId": "w"},
        {"eventSessionId": "foreign"},
        {"basedOnWorldVersion": 999},
    ],
)
def test_model_draft_cannot_author_runtime_identity(identity: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ProposalDraft.model_validate_json(
            json.dumps(
                {
                    **identity,
                    "action": {"kind": "no_op", "nextWakeup": "event_change"},
                }
            ),
            strict=True,
        )


def test_proposal_round_trip_is_strict_and_requires_world_identity() -> None:
    proposal = build_action_proposal(
        world_ref=WORLD_REF,
        spec=_anon_spec(),
        view=_view(affordances=()),
        proposal_id="same-id",
        draft=ProposalDraft(action=NoOpAction(next_wakeup="event_change")),
    )
    wire = proposal.model_dump_json(by_alias=True)
    assert ActionProposal.model_validate_json(wire, strict=True) == proposal
    with pytest.raises(ValidationError, match="frozen"):
        proposal.world_ref = WorldRef(project_id="p", world_id="w")
    data = proposal.model_dump(by_alias=False)
    del data["world_ref"]
    with pytest.raises(ValidationError, match="Field required"):
        ActionProposal.model_validate(data, strict=True)


def _catalog() -> Catalog:
    skills = RuntimeSkillCatalog.load(SKILLS_PATH).skills
    return Catalog(
        tools=(
            ToolDefinition(
                id="visible_location.query",
                version="1",
                mode=ToolMode.QUERY,
            ),
            ToolDefinition(id="world.commit", version="1", mode=ToolMode.MUTATE),
        ),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills,
    )


def _skill(
    skill_id: str,
    version: str,
    *,
    agent_kind: Literal["character", "director", "broadcast"] = "character",
    body: str | None = None,
) -> RuntimeSkill:
    skill_body = body or f"Fixture Skill for {skill_id}."
    source_text = (
        "---\n"
        f"skill_id: {skill_id}\n"
        f"version: {version}\n"
        f"agent_kind: {agent_kind}\n"
        "---\n"
        f"{skill_body}\n"
    )
    return RuntimeSkill(
        skill_id=skill_id,
        version=version,
        agent_kind=agent_kind,
        body=skill_body,
        content_hash=sha256(source_text.encode("utf-8")).hexdigest(),
        source_path=f"fixture://{skill_id}@{version}",
        source_text=source_text,
    )


def _anon_spec() -> CompiledPersonActSpec:
    return compile_manifest(load_manifest(FIXTURE_PATH), _catalog())[0]


def _view(
    *,
    affordances: tuple[Affordance, ...],
    visible_evidence_ids: tuple[str, ...] = ("queue-ready",),
) -> AgentView:
    return AgentView(
        world_ref=WORLD_REF,
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        based_on_control_epoch=1,
        based_on_decision_seq=0,
        current_location_id="cafe",
        visible_evidence_ids=visible_evidence_ids,
        affordances=affordances,
    )


def _replace_first_agent(
    manifest: Manifest,
    *,
    capabilities: CapabilityRequest | None = None,
    prompt_profile: str | None = None,
) -> Manifest:
    current = manifest.agents[0]
    first = NPCDefinition(
        id=current.id,
        display_name=current.display_name,
        persona=current.persona,
        memory=current.memory,
        capabilities=capabilities or current.capabilities,
        behavior=current.behavior,
        character_skill=current.character_skill,
        prompt_profile=prompt_profile or current.prompt_profile,
    )
    return Manifest(
        format_version=manifest.format_version,
        project_id=manifest.project_id,
        agents=(first, *manifest.agents[1:]),
    )
