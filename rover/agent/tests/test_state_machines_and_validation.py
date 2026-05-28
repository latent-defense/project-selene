from pathlib import Path
import sys
import types

agent_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(agent_dir))
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = object
anthropic_stub.NotFoundError = Exception
sys.modules.setdefault("anthropic", anthropic_stub)
httpx_stub = types.ModuleType("httpx")
httpx_stub.Client = object
httpx_stub.ConnectError = Exception
httpx_stub.Response = object
sys.modules.setdefault("httpx", httpx_stub)

from mapping.models import MappingState
from mapping.state_machine import MappingStateMachine
from reporting.consistency import build_consistency_checks
from reporting.metrics import compute_dependency_metrics
from reporting.models import ReportingState
from reporting.state_machine import ReportingStateMachine
from reporting.validation import validate_citation_schema


def test_mapping_state_machine_transitions_complete() -> None:
    artifact = {"ok": True}
    writes: list[dict[str, object]] = []

    def write_artifact(payload: dict[str, object]) -> None:
        writes.append(payload)

    def handle_init(context):
        return MappingState.DISCOVER

    def handle_discover(context):
        return MappingState.COLLECT

    def handle_collect(context):
        return MappingState.FINALIZE_ARTIFACT

    def handle_finalize(context):
        context.artifact = artifact
        return MappingState.COMPLETE

    machine = MappingStateMachine(
        write_artifact_fn=write_artifact,
        handlers={
            MappingState.INIT: handle_init,
            MappingState.DISCOVER: handle_discover,
            MappingState.COLLECT: handle_collect,
            MappingState.FINALIZE_ARTIFACT: handle_finalize,
        },
    )
    output = machine.run()

    assert output == artifact
    assert writes == [artifact]
    assert machine.context.state == MappingState.COMPLETE
    assert machine.context.transitions == [
        "INIT",
        "DISCOVER",
        "COLLECT",
        "FINALIZE_ARTIFACT",
        "COMPLETE",
    ]


def test_mapping_state_machine_failure_transition() -> None:
    def handle_init(context):
        raise RuntimeError("boom")

    machine = MappingStateMachine(
        write_artifact_fn=lambda payload: None,
        handlers={MappingState.INIT: handle_init},
    )
    try:
        machine.run()
        assert False, "Expected mapping state machine to fail"
    except RuntimeError as exc:
        assert "boom" in str(exc)
    assert machine.context.state == MappingState.FAILED
    assert machine.context.transitions[-1] == "FAILED"


def test_reporting_state_machine_transitions_complete() -> None:
    def handle_load(context):
        return ReportingState.ANALYZE

    def handle_analyze(context):
        return ReportingState.SYNTHESIZE_NARRATIVE

    def handle_synthesize(context):
        context.report_content = "report body"
        return ReportingState.VALIDATE

    def handle_validate(context):
        return ReportingState.WRITE

    def handle_write(context):
        context.report_content = "final report"
        context.report_length = len(context.report_content)
        return ReportingState.COMPLETE

    machine = ReportingStateMachine(
        handlers={
            ReportingState.LOAD_AND_NORMALIZE: handle_load,
            ReportingState.ANALYZE: handle_analyze,
            ReportingState.SYNTHESIZE_NARRATIVE: handle_synthesize,
            ReportingState.VALIDATE: handle_validate,
            ReportingState.WRITE: handle_write,
        }
    )
    output = machine.run()

    assert output == "final report"
    assert machine.context.state == ReportingState.COMPLETE
    assert machine.context.transitions == [
        "LOAD_AND_NORMALIZE",
        "ANALYZE",
        "SYNTHESIZE_NARRATIVE",
        "VALIDATE",
        "WRITE",
        "COMPLETE",
    ]


def test_reporting_state_machine_failure_transition() -> None:
    def handle_load(context):
        raise ValueError("invalid map")

    machine = ReportingStateMachine(
        handlers={ReportingState.LOAD_AND_NORMALIZE: handle_load}
    )
    try:
        machine.run()
        assert False, "Expected reporting state machine to fail"
    except ValueError as exc:
        assert "invalid map" in str(exc)
    assert machine.context.state == ReportingState.FAILED
    assert machine.context.transitions[-1] == "FAILED"


def test_citation_schema_validation_rejects_malformed_lines() -> None:
    invalid = "Evidence: metric:foo; endpoint:/bar; source:baz"
    try:
        validate_citation_schema(invalid)
        assert False, "Expected citation schema validation to fail"
    except ValueError as exc:
        assert "Invalid citation schema lines found" in str(exc)


def test_metrics_and_consistency_smoke() -> None:
    dependency_graph = [
        {"from_pod": "a", "to_pod": "b", "endpoint": "/x", "criticality": "high"},
        {"from_pod": "c", "to_pod": "b", "endpoint": "/y", "criticality": "medium"},
    ]
    supply_graph = [{"from_pod": "b", "to_pod": "a", "resource": "water"}]
    discovered_pods = {"a", "b", "c"}
    endpoint_coverage = {"a": {"/status"}, "b": {"/status"}, "c": {"/status"}}

    metrics = compute_dependency_metrics(
        dependency_graph=dependency_graph,
        discovered_pods=discovered_pods,
    )
    consistency = build_consistency_checks(
        dependency_graph=dependency_graph,
        supply_graph=supply_graph,
        discovered_pods=discovered_pods,
        endpoint_coverage=endpoint_coverage,
    )

    assert metrics["top_depended_pods"][0]["pod_id"] == "b"
    assert "summary" in consistency
