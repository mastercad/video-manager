from __future__ import annotations

import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.app import ConverterApp, _planned_job_steps
from src.media.converter import ConvertJob
from src.settings import AppSettings
from src.workflow import FileEntry, Workflow, WorkflowJob
from src.runtime.workflow_executor import WorkflowExecutor
from src.workflow_steps import ExecutorSupport, OutputStepStack, PreparedOutput


def _run_stack(stack, executor, prepared, yt_service=None, kb_sort_index=None):
    failures = stack.execute_processing_steps(executor, prepared)
    if not failures and not executor._cancel.is_set():
        failures += stack.execute_delivery_steps(executor, prepared, yt_service, kb_sort_index or {})
    return failures

_app = QApplication.instance() or QApplication(sys.argv)


@dataclass(frozen=True)
class StepScenario:
    convert: bool
    merge: bool
    titlecard: bool
    yt_version: bool
    youtube_upload: bool
    kaderblick: bool
    expected_steps: tuple[str, ...]

    @property
    def id(self) -> str:
        flags = [
            ("c", self.convert),
            ("m", self.merge),
            ("t", self.titlecard),
            ("y", self.yt_version),
            ("u", self.youtube_upload),
            ("k", self.kaderblick),
        ]
        return "-".join(name for name, enabled in flags if enabled) or "transfer-only"


def _build_valid_scenarios() -> list[StepScenario]:
    scenarios: list[StepScenario] = []
    for convert, merge, titlecard, yt_version, youtube_upload, kaderblick in product([False, True], repeat=6):
        if kaderblick and not youtube_upload:
            continue
        has_output_stack = convert or merge or youtube_upload
        if titlecard and not has_output_stack:
            continue
        if yt_version and not has_output_stack:
            continue

        steps = ["transfer"]
        if convert:
            steps.append("convert")
        if merge:
            steps.append("merge")
        if has_output_stack and titlecard:
            steps.append("titlecard")
        if has_output_stack and yt_version:
            steps.append("yt_version")
        if has_output_stack and youtube_upload:
            steps.append("youtube_upload")
        if has_output_stack and youtube_upload and kaderblick:
            steps.append("kaderblick")

        scenarios.append(
            StepScenario(
                convert=convert,
                merge=merge,
                titlecard=titlecard,
                yt_version=yt_version,
                youtube_upload=youtube_upload,
                kaderblick=kaderblick,
                expected_steps=tuple(steps),
            )
        )
    return scenarios


ALL_STEP_SCENARIOS = _build_valid_scenarios()


def _graph_for_scenario(scenario: StepScenario) -> tuple[list, list]:
    nodes: list = [{"id": "src-1", "type": "source_files"}]
    edges: list = []
    prev = "src-1"
    if scenario.convert:
        nodes.append({"id": "conv-1", "type": "convert"})
        edges.append({"source": prev, "target": "conv-1"})
        prev = "conv-1"
    if scenario.merge:
        nodes.append({"id": "merge-1", "type": "merge"})
        edges.append({"source": prev, "target": "merge-1"})
        prev = "merge-1"
    if scenario.titlecard:
        nodes.append({"id": "title-1", "type": "titlecard"})
        edges.append({"source": prev, "target": "title-1"})
        prev = "title-1"
    if scenario.yt_version:
        nodes.append({"id": "ytv-1", "type": "yt_version"})
        edges.append({"source": prev, "target": "ytv-1"})
        prev = "ytv-1"
    if scenario.youtube_upload:
        nodes.append({"id": "ytu-1", "type": "youtube_upload"})
        edges.append({"source": prev, "target": "ytu-1"})
        prev = "ytu-1"
    if scenario.kaderblick:
        nodes.append({"id": "kb-1", "type": "kaderblick"})
        edges.append({"source": prev, "target": "kb-1"})
    return nodes, edges


OUTPUT_STEP_SCENARIOS = [
    scenario
    for scenario in ALL_STEP_SCENARIOS
    if any(step in scenario.expected_steps for step in ("titlecard", "yt_version", "youtube_upload", "kaderblick"))
]


class _DummySignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)


class _DummyThread:
    def __init__(self, parent=None):
        self.parent = parent
        self.started = _DummySignal()

    def isRunning(self):
        return False

    def start(self):
        return None

    def quit(self):
        return None

    def wait(self, timeout=None):
        return True


class _DummyExecutor:
    def __init__(self, workflow, settings, active_indices=None, **_kwargs):
        self.workflow = workflow
        self.settings = settings
        self.active_indices = active_indices or []
        self.log_message = _DummySignal()
        self.job_status = _DummySignal()
        self.job_progress = _DummySignal()
        self.file_progress = _DummySignal()
        self.overall_progress = _DummySignal()
        self.phase_changed = _DummySignal()
        self.finished = _DummySignal()

    def moveToThread(self, thread):
        self.thread = thread

    def run(self):
        return None

    def cancel(self):
        return None


class _FakeExecutor:
    def __init__(self):
        import threading

        self._cancel = threading.Event()
        self._concat_func = lambda *args, **kwargs: True
        self._youtube_convert_func = lambda *args, **kwargs: True
        self.log_message = _Emitter()
        self.phase_changed = _Emitter()
        self.job_progress = _Emitter()
        self.status_updates = []
        self.step_details = []

    def _set_step_status(self, job, step, status):
        if not isinstance(job.step_statuses, dict):
            job.step_statuses = {}
        job.step_statuses[step] = status

    def _set_job_status(self, orig_idx, status):
        self.status_updates.append((orig_idx, status))

    def _set_step_detail(self, job, step, detail):
        if not isinstance(job.step_details, dict):
            job.step_details = {}
        job.step_details[step] = detail
        self.step_details.append((step, detail))

    @staticmethod
    def _find_file_entry(job, file_path):
        target = Path(file_path).name
        for entry in job.files:
            if Path(entry.source_path).name == target:
                return entry
        return None

    @staticmethod
    def _prepared_output_reaches_type(prepared, target_type):
        return ExecutorSupport.prepared_output_reaches_type(prepared, target_type)

    @staticmethod
    def _graph_node_id_for_type(job, node_type):
        return ExecutorSupport.graph_node_id_for_type(job, node_type)

    @staticmethod
    def _validation_branch_has_targets(prepared, node_type, branch):
        return ExecutorSupport.validation_branch_has_targets(prepared, node_type, branch)


class _Emitter:
    def __init__(self):
        self.values = []

    def emit(self, value, *args):
        self.values.append((value, *args))


def _new_app() -> ConverterApp:
    with patch("src.app.AppSettings.load", return_value=AppSettings()):
        return ConverterApp()


def _job_for_scenario(scenario: StepScenario, *, existing_paths: list[str] | None = None) -> WorkflowJob:
    nodes, edges = _graph_for_scenario(scenario)
    job = WorkflowJob(
        name=f"Job {scenario.id}",
        source_mode="files",
        convert_enabled=scenario.convert,
        title_card_enabled=scenario.titlecard,
        create_youtube_version=scenario.yt_version,
        upload_youtube=scenario.youtube_upload,
        upload_kaderblick=scenario.kaderblick,
        default_kaderblick_game_id="game-1" if scenario.kaderblick else "",
        default_kaderblick_video_type_id=4 if scenario.kaderblick else 0,
        default_kaderblick_camera_id=8 if scenario.kaderblick else 0,
        graph_nodes=nodes,
        graph_edges=edges,
    )
    paths = existing_paths or [f"/tmp/{scenario.id}_1.mp4"]
    if scenario.merge and len(paths) == 1:
        paths = [f"/tmp/{scenario.id}_1.mp4", f"/tmp/{scenario.id}_2.mp4"]
    merge_group_id = "g1" if scenario.merge else ""
    job.files = [
        FileEntry(source_path=path, merge_group_id=merge_group_id)
        for path in paths
    ]
    return job


def _runtime_job_for_scenario(tmp_path: Path, scenario: StepScenario) -> WorkflowJob:
    ext = ".mjpeg" if scenario.convert else ".mp4"
    count = 2 if scenario.merge else 1
    paths = []
    for idx in range(count):
        src = tmp_path / f"{scenario.id}_{idx + 1}{ext}"
        src.write_text("src", encoding="utf-8")
        paths.append(str(src))
    return _job_for_scenario(scenario, existing_paths=paths)


def _fake_convert(cv_job, settings, **_kwargs):
    ext = "mp4" if settings.video.output_format == "mp4" else "avi"
    out = cv_job.output_path or cv_job.source_path.with_suffix(f".{ext}")
    if out == cv_job.source_path:
        out = cv_job.source_path.with_stem(f"{cv_job.source_path.stem}_converted").with_suffix(f".{ext}")
    out.write_text("converted", encoding="utf-8")
    cv_job.output_path = out
    cv_job.status = "Fertig"
    return True


def _fake_concat(_sources, dest, **_kwargs):
    dest.write_text("concat", encoding="utf-8")
    return True


def _fake_youtube_convert(cv_job, *_args, **_kwargs):
    yt_path = cv_job.output_path.with_stem(cv_job.output_path.stem + "_youtube")
    yt_path.write_text("youtube", encoding="utf-8")
    return True


def _fake_generate_title_card(output_path, *_args, **_kwargs):
    Path(output_path).write_text("intro", encoding="utf-8")
    return True


def _prepared_output_for_scenario(tmp_path: Path, scenario: StepScenario) -> PreparedOutput:
    source = tmp_path / f"{scenario.id}_source.mp4"
    output = tmp_path / f"{scenario.id}_output.mp4"
    source.write_text("src", encoding="utf-8")
    output.write_text("out", encoding="utf-8")
    nodes, edges = _graph_for_scenario(scenario)
    job = WorkflowJob(
        title_card_enabled=scenario.titlecard,
        create_youtube_version=scenario.yt_version,
        upload_youtube=scenario.youtube_upload,
        upload_kaderblick=scenario.kaderblick,
        default_kaderblick_game_id="game-1",
        default_kaderblick_video_type_id=4,
        default_kaderblick_camera_id=8,
        files=[FileEntry(source_path=str(source), kaderblick_game_id="game-1")],
        graph_nodes=nodes,
        graph_edges=edges,
    )
    cv_job = ConvertJob(source_path=source, output_path=output, youtube_title="Titel")
    return PreparedOutput(
        orig_idx=0,
        job=job,
        cv_job=cv_job,
        per_settings=AppSettings(),
        mark_finished=True,
        graph_origin_node_id="src-1",
        title_card_enabled_override=scenario.titlecard,
        repair_enabled_override=False,
        youtube_version_enabled_override=scenario.yt_version,
        youtube_upload_enabled_override=scenario.youtube_upload,
        kaderblick_enabled_override=(scenario.youtube_upload and scenario.kaderblick),
    )


@pytest.mark.parametrize("scenario", ALL_STEP_SCENARIOS, ids=lambda scenario: scenario.id)
def test_planned_job_steps_matrix(scenario: StepScenario):
    job = _job_for_scenario(scenario)
    assert _planned_job_steps(job) == list(scenario.expected_steps)


@pytest.mark.parametrize("scenario", ALL_STEP_SCENARIOS, ids=lambda scenario: f"restart-{scenario.id}")
def test_restart_clears_resume_state_for_all_step_constellations(scenario: StepScenario):
    window = _new_app()
    try:
        job = _job_for_scenario(scenario)
        job.resume_status = "Fortsetzen"
        job.step_statuses = {step: "done" for step in scenario.expected_steps}
        window._workflow = Workflow(jobs=[job])
        window._refresh_table()
        window._save_last_workflow = MagicMock()

        with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.No), patch(
            "src.app.QThread", _DummyThread
        ), patch("src.app.WorkflowExecutor", _DummyExecutor):
            window._start_workflow()

        assert job.resume_status == ""
        assert job.step_statuses == {}
        assert job.progress_pct == 0
        assert job.overall_progress_pct == 0
        assert job.current_step_key == ""
    finally:
        window.close()


@pytest.mark.parametrize("scenario", ALL_STEP_SCENARIOS, ids=lambda scenario: f"resume-{scenario.id}")
def test_resume_preserves_state_for_all_step_constellations(scenario: StepScenario):
    window = _new_app()
    try:
        job = _job_for_scenario(scenario)
        job.resume_status = "Fortsetzen"
        job.step_statuses = {step: "done" for step in scenario.expected_steps}
        window._workflow = Workflow(jobs=[job])
        window._refresh_table()
        window._save_last_workflow = MagicMock()

        with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.Yes), patch(
            "src.app.QThread", _DummyThread
        ), patch("src.app.WorkflowExecutor", _DummyExecutor):
            window._start_workflow()

        assert job.resume_status == "Fortsetzen"
        assert job.step_statuses == {step: "done" for step in scenario.expected_steps}
    finally:
        window.close()


@pytest.mark.parametrize("scenario", ALL_STEP_SCENARIOS, ids=lambda scenario: f"complete-{scenario.id}")
def test_workflow_completion_matrix(tmp_path, scenario: StepScenario):
    job = _runtime_job_for_scenario(tmp_path, scenario)
    workflow = Workflow(jobs=[job])
    executor = WorkflowExecutor(workflow, AppSettings())
    executor._convert_func = _fake_convert
    executor._concat_func = _fake_concat
    executor._youtube_convert_func = _fake_youtube_convert
    finished = []
    executor.finished.connect(lambda ok, skip, fail: finished.append((ok, skip, fail)))

    with patch("src.workflow_steps.title_card_step.generate_title_card", side_effect=_fake_generate_title_card), patch(
        "src.runtime.workflow_executor.get_youtube_service",
        return_value=MagicMock() if scenario.youtube_upload else None,
    ), patch(
        "src.workflow_steps.youtube_upload_step.get_video_id_for_output",
        return_value=None,
    ), patch(
        "src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube",
        return_value=True,
    ), patch(
        "src.workflow_steps.kaderblick_post_step.get_video_id_for_output",
        return_value="video-123",
    ), patch(
        "src.workflow_steps.kaderblick_post_step.KaderblickPostStep._post_to_kaderblick",
        return_value=True,
    ), patch(
        "src.runtime.workflow_executor.support.publish_game_videos",
        return_value={},
    ):
        executor.run()

    assert finished
    assert finished[0][2] == 0
    assert job.step_statuses.get("transfer") == "done"
    for step in scenario.expected_steps:
        assert job.step_statuses.get(step) == "done"


@pytest.mark.parametrize("scenario", OUTPUT_STEP_SCENARIOS, ids=lambda scenario: f"reuse-{scenario.id}")
def test_output_step_resume_matrix(tmp_path, scenario: StepScenario):
    stack = OutputStepStack()
    executor = _FakeExecutor()
    prepared = _prepared_output_for_scenario(tmp_path, scenario)

    current_output = prepared.cv_job.output_path
    if scenario.titlecard:
        titlecard = current_output.with_stem(current_output.stem + "_titlecard")
        titlecard.write_text("titlecard", encoding="utf-8")
        current_output = titlecard
    if scenario.yt_version:
        yt_version = current_output.with_stem(current_output.stem + "_youtube")
        yt_version.write_text("youtube", encoding="utf-8")

    executor._youtube_convert_func = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("yt version should be reused, not regenerated")
    )

    with patch(
        "src.workflow_steps.title_card_step.TitleCardStep._prepend_title_card",
        side_effect=AssertionError("titlecard should be reused, not regenerated"),
    ), patch(
        "src.workflow_steps.youtube_version_step.validate_media_output",
        return_value=True,
    ), patch(
        "src.workflow_steps.youtube_upload_step.get_video_id_for_output",
        return_value="video-123" if scenario.youtube_upload else None,
    ), patch(
        "src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube",
        side_effect=AssertionError("upload should be reused, not rerun"),
    ), patch(
        "src.workflow_steps.kaderblick_post_step.get_video_id_for_output",
        return_value="video-123" if scenario.kaderblick else None,
    ), patch(
        "src.workflow_steps.kaderblick_post_step.get_recorded_kaderblick_id",
        return_value=99 if scenario.kaderblick else None,
    ), patch(
        "src.workflow_steps.kaderblick_post_step.KaderblickPostStep._post_to_kaderblick",
        side_effect=AssertionError("kaderblick should be reused, not reposted"),
    ):
        failures = _run_stack(
            stack,
            executor,
            prepared,
            yt_service=object() if scenario.youtube_upload else None,
            kb_sort_index={},
        )

    assert failures == 0
    for step in scenario.expected_steps:
        if step in {"transfer", "convert", "merge"}:
            continue
        assert prepared.job.step_statuses.get(step) == "reused-target"
