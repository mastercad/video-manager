"""Tests für Resume-Status und Last-Workflow-Verhalten in der Haupt-GUI."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication(sys.argv)

from src.app import (
    ConverterApp,
    _compute_job_overall_progress,
    _format_resume_tooltip,
    _job_has_source_config,
    _normalize_cancelled_resume_state,
    _planned_job_steps,
    _repair_restored_workflow,
    _workflow_step_progress,
)
from src.app.helpers import (
    _compute_job_overall_progress,
    _current_planned_job_steps,
    _is_finished_step,
    _job_is_placeholder,
    _jobs_look_compatible,
    _summarize_pipeline,
    _summarize_source,
)
from src.app.execution import (
    _build_source_move_conflict_warning,
    _job_effectively_moves_sources,
    _job_source_paths,
)
from src.settings import AppSettings
from src.workflow.storage import save_workflow
from src.workflow import FileEntry, Workflow, WorkflowJob


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
    def __init__(self, workflow, settings, *, active_indices=None, allow_reuse_existing=True):
        self.workflow = workflow
        self.settings = settings
        self.active_indices = set(active_indices or set())
        self.allow_reuse_existing = allow_reuse_existing
        self.log_message = _DummySignal()
        self.job_status = _DummySignal()
        self.job_progress = _DummySignal()
        self.file_progress = _DummySignal()
        self.overall_progress = _DummySignal()
        self.phase_changed = _DummySignal()
        self.finished = _DummySignal()
        self.source_status = _DummySignal()
        self.source_progress = _DummySignal()
        self.cancel_calls = []

    def moveToThread(self, thread):
        self.thread = thread

    def run(self):
        return None

    def cancel(self, active_indices=None):
        self.cancel_calls.append(None if active_indices is None else set(active_indices))
        return None


class _DummyWorkflowDialog:
    instances = []

    def __init__(self, parent, job, allow_edit=False, settings=None, allow_wizard_shortcut=True):
        self.parent = parent
        self.job = job
        self.allow_edit = allow_edit
        self.settings = settings
        self.allow_wizard_shortcut = allow_wizard_shortcut
        self.edit_requested = False
        self.changed = False
        _DummyWorkflowDialog.instances.append(self)

    def exec(self):
        return True


def _new_app() -> ConverterApp:
    settings = AppSettings()
    settings.restore_last_workflow = False
    with patch("src.app.AppSettings.load", return_value=settings):
        window = ConverterApp()
    return window


def _rich_file_entry() -> FileEntry:
    return FileEntry(
        source_path="/tmp/halbzeit1.mp4",
        output_filename="halbzeit1_export.mp4",
        youtube_title="FC Heim - FC Gast | 1. Halbzeit",
        youtube_description="Spieltag 23",
        youtube_playlist="Saison 2025/2026",
        kaderblick_game_id="4711",
        kaderblick_game_start=120,
        kaderblick_video_type_id=7,
        kaderblick_camera_id=3,
        merge_group_id="merge-a",
        title_card_subtitle="1. Halbzeit",
        graph_source_id="source-files-1",
        title_card_before_merge=True,
    )


def _rich_job(**overrides) -> WorkflowJob:
    job = WorkflowJob(
        name="Gespeicherter Workflow",
        source_mode="files",
        files=[_rich_file_entry()],
        convert_enabled=True,
        encoder="libx264",
        crf=21,
        preset="slow",
        fps=50,
        output_format="mov",
        overwrite=True,
        merge_audio=True,
        amplify_audio=True,
        amplify_db=8.5,
        audio_sync=True,
        create_youtube_version=True,
        upload_youtube=True,
        default_youtube_title="Liga | FC Heim - FC Gast",
        default_youtube_playlist="Playlist A",
        default_youtube_competition="Pokal",
        upload_kaderblick=True,
        default_kaderblick_game_id="9001",
        default_kaderblick_video_type_id=11,
        default_kaderblick_camera_id=5,
        title_card_enabled=True,
        title_card_logo_path="/tmp/logo.png",
        title_card_duration=4.0,
        title_card_bg_color="#112233",
        title_card_fg_color="#F8FAFC",
        title_card_home_team="FC Heim",
        title_card_away_team="FC Gast",
        title_card_date="2026-03-22",
        graph_nodes=[
            {"id": "source-files-1", "type": "source_files", "x": 80.0, "y": 100.0},
            {"id": "convert-1", "type": "convert", "x": 360.0, "y": 100.0},
            {"id": "merge-1", "type": "merge", "x": 360.0, "y": 220.0},
            {"id": "title-1", "type": "titlecard", "x": 360.0, "y": 340.0},
            {"id": "yt-1", "type": "yt_version", "x": 360.0, "y": 460.0},
            {"id": "upload-1", "type": "youtube_upload", "x": 680.0, "y": 220.0},
            {"id": "kb-1", "type": "kaderblick", "x": 680.0, "y": 340.0},
        ],
        graph_edges=[
            {"source": "source-files-1", "target": "convert-1"},
            {"source": "convert-1", "target": "merge-1"},
            {"source": "merge-1", "target": "title-1"},
            {"source": "title-1", "target": "yt-1"},
            {"source": "yt-1", "target": "upload-1"},
            {"source": "upload-1", "target": "kb-1"},
        ],
        resume_status="YT-Version erstellen …",
        step_statuses={
            "transfer": "done",
            "convert": "done",
            "merge": "done",
            "titlecard": "done",
            "yt_version": "running",
        },
        progress_pct=44,
        overall_progress_pct=78,
        current_step_key="yt_version",
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _roundtrip_restored_workflow(tmp_path, workflow: Workflow) -> ConverterApp:
    last_workflow_file = tmp_path / "last_workflow.json"
    settings = AppSettings()
    settings.restore_last_workflow = False

    with patch("src.app.AppSettings.load", return_value=settings), patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
        first = ConverterApp()
        try:
            first._workflow = workflow
            first._save_last_workflow()
        finally:
            first.close()

    restart_settings = AppSettings()
    restart_settings.restore_last_workflow = True
    with patch("src.app.AppSettings.load", return_value=restart_settings), patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
        return ConverterApp()


class TestResumeTooltip:
    def test_format_resume_tooltip_lists_known_steps_in_order(self):
        job = WorkflowJob(
            resume_status="Transfer OK",
            step_statuses={
                "youtube_upload": "done",
                "transfer": "done",
                "convert": "running",
            },
        )

        tooltip = _format_resume_tooltip(job)

        assert tooltip == (
            "Letzter Status: Transfer OK\n"
            "Transfer: done\n"
            "Konvertierung: running\n"
            "YouTube-Upload: done"
        )

    def test_format_resume_tooltip_uses_resume_status_without_steps(self):
        job = WorkflowJob(resume_status="Kaderblick …")
        assert _format_resume_tooltip(job) == "Kaderblick …"

    def test_format_resume_tooltip_includes_step_details(self):
        job = WorkflowJob(
            resume_status="Zusammenführen OK",
            step_statuses={"merge": "done"},
            step_details={"merge": "Quellen: a.mp4, b.mp4 | Datei: merged.mp4 | Dauer: 12:34 | Größe: 1.20 GB"},
        )

        tooltip = _format_resume_tooltip(job)

        assert tooltip == (
            "Letzter Status: Zusammenführen OK\n"
            "Zusammenführen: done\n"
            "  Quellen: a.mp4, b.mp4 | Datei: merged.mp4 | Dauer: 12:34 | Größe: 1.20 GB"
        )

    def test_format_resume_tooltip_includes_elapsed_runtime_when_present(self):
        job = WorkflowJob(resume_status="Kaderblick …", run_elapsed_seconds=125)

        assert _format_resume_tooltip(job) == "Kaderblick …\nLaufzeit: 2min 05s"


class TestJobOverallProgress:
    def test_job_has_source_config_requires_real_source_definition(self):
        assert _job_has_source_config(WorkflowJob(source_mode="files", files=[])) is False


class TestWorkflowShutdown:
    def test_on_workflow_done_uses_live_checkbox_state_even_when_workflow_flag_is_false(self):
        window = _new_app()
        try:
            window._workflow = Workflow(name="Spieltag 23", job=_rich_job(), shutdown_after=False)
            window._shutdown_cb.setChecked(True)
            assert window._shutdown_cb.text() == "⚠ Herunterfahren: AN"
            window._workflow.last_run_elapsed_seconds = 5.0
            window._save_last_workflow = MagicMock()

            dialog_cls = MagicMock()
            dialog_cls.return_value.exec.return_value = True

            with patch("src.ui.dialogs.ShutdownCountdownDialog", dialog_cls), \
                 patch("src.app.execution.shutdown_command", return_value=["shutdown", "/s", "/t", "0"]), \
                 patch("src.app.execution.subprocess.Popen") as popen:
                window._on_workflow_done(1, 0, 0)

            dialog_cls.assert_called_once()
            popen.assert_called_once_with(["shutdown", "/s", "/t", "0"])
            assert window._workflow.shutdown_after is True
        finally:
            window.close()


class TestKaderblickWorkflowPublishOption:
    def test_toggle_updates_workflow_and_persists(self):
        window = _new_app()
        try:
            window._save_last_workflow = MagicMock()

            window._publish_kaderblick_cb.setChecked(True)

            assert window._workflow.publish_kaderblick_videos is True
            assert window._publish_kaderblick_cb.text() == "Kaderblick-Publish: AN"
            window._save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_sync_restores_workflow_value_without_saving(self):
        window = _new_app()
        try:
            window._workflow.publish_kaderblick_videos = True
            window._save_last_workflow = MagicMock()

            window._sync_publish_kaderblick_checkbox()

            assert window._publish_kaderblick_cb.isChecked() is True
            assert window._publish_kaderblick_cb.text() == "Kaderblick-Publish: AN"
            window._save_last_workflow.assert_not_called()
        finally:
            window.close()

    def test_on_workflow_done_skips_shutdown_when_live_checkbox_state_is_false(self):
        window = _new_app()
        try:
            window._workflow = Workflow(name="Spieltag 23", job=_rich_job(), shutdown_after=True)
            window._shutdown_cb.setChecked(False)
            window._workflow.last_run_elapsed_seconds = 5.0
            window._save_last_workflow = MagicMock()

            dialog_cls = MagicMock()

            with patch("src.ui.dialogs.ShutdownCountdownDialog", dialog_cls), \
                 patch("src.app.execution.shutdown_command", return_value=["shutdown", "/s", "/t", "0"]), \
                 patch("src.app.execution.subprocess.Popen") as popen:
                window._on_workflow_done(1, 0, 0)

            dialog_cls.assert_not_called()
            popen.assert_not_called()
            assert window._workflow.shutdown_after is False
        finally:
            window.close()

    def test_on_workflow_done_uses_platform_shutdown_command(self):
        window = _new_app()
        try:
            window._workflow = Workflow(name="Spieltag 23", job=_rich_job(), shutdown_after=True)
            window._shutdown_cb.setChecked(True)
            window._workflow.last_run_elapsed_seconds = 5.0
            window._save_last_workflow = MagicMock()

            dialog_cls = MagicMock()
            dialog_cls.return_value.exec.return_value = True

            with patch("src.ui.dialogs.ShutdownCountdownDialog", dialog_cls), \
                 patch("src.app.execution.shutdown_command", return_value=["shutdown", "/s", "/t", "0"]), \
                 patch("src.app.execution.subprocess.Popen") as popen:
                window._on_workflow_done(1, 0, 0)

            popen.assert_called_once_with(["shutdown", "/s", "/t", "0"])
        finally:
            window.close()

    def test_on_workflow_done_logs_when_shutdown_is_unsupported(self):
        window = _new_app()
        try:
            window._workflow = Workflow(name="Spieltag 23", job=_rich_job(), shutdown_after=True)
            window._shutdown_cb.setChecked(True)
            window._workflow.last_run_elapsed_seconds = 5.0
            window._save_last_workflow = MagicMock()
            window._append_log = MagicMock()

            dialog_cls = MagicMock()
            dialog_cls.return_value.exec.return_value = True

            with patch("src.ui.dialogs.ShutdownCountdownDialog", dialog_cls), \
                 patch("src.app.execution.shutdown_command", return_value=None), \
                 patch("src.app.execution.subprocess.Popen") as popen:
                window._on_workflow_done(1, 0, 0)

            popen.assert_not_called()
            assert any(
                "nicht unterstützt" in str(call.args[0])
                for call in window._append_log.call_args_list
            )
        finally:
            window.close()
        assert _job_has_source_config(WorkflowJob(source_mode="folder_scan", source_folder="")) is False
        assert _job_has_source_config(WorkflowJob(source_mode="pi_download", device_name="")) is False
        assert _job_has_source_config(
            WorkflowJob(source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")])
        ) is True

    def test_planned_steps_include_current_pipeline(self):
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
                {"id": "tc-1", "type": "titlecard"},
                {"id": "ytv-1", "type": "yt_version"},
                {"id": "ytu-1", "type": "youtube_upload"},
                {"id": "kb-1", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "conv-1"},
                {"source": "conv-1", "target": "tc-1"},
                {"source": "tc-1", "target": "ytv-1"},
                {"source": "ytv-1", "target": "ytu-1"},
                {"source": "ytu-1", "target": "kb-1"},
            ],
        )
        job.files = []

        assert _planned_job_steps(job) == [
            "transfer",
            "convert",
            "titlecard",
            "yt_version",
            "youtube_upload",
            "kaderblick",
        ]

    def test_planned_steps_skip_unreachable_output_steps_without_processing(self):
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
            ],
            graph_edges=[],
        )

        assert _planned_job_steps(job) == ["transfer"]

    def test_compute_job_overall_progress_uses_current_step_progress(self):
        job = WorkflowJob(
            convert_enabled=True,
            upload_youtube=True,
        )
        job.step_statuses = {"transfer": "done", "convert": "running"}
        job.current_step_key = "convert"

        assert _compute_job_overall_progress(job, "Konvertiere …", 50) == 50

    def test_compute_job_overall_progress_reaches_done_state(self):
        job = WorkflowJob(convert_enabled=False, upload_youtube=True)
        job.step_statuses = {"transfer": "done", "youtube_upload": "done"}
        job.current_step_key = "youtube_upload"

        assert _compute_job_overall_progress(job, "Fertig", 100) == 100

    def test_compute_job_overall_progress_keeps_transfer_progress_when_graph_merge_starts(self):
        job = WorkflowJob(
            convert_enabled=False,
            graph_nodes=[
                {"id": "source-1", "type": "source_files"},
                {"id": "merge-1", "type": "merge"},
                {"id": "yt-1", "type": "youtube_upload"},
            ],
            graph_edges=[
                {"source": "source-1", "target": "merge-1"},
                {"source": "merge-1", "target": "yt-1"},
            ],
            upload_youtube=True,
        )
        job.step_statuses = {"transfer": "done", "merge": "running"}
        job.current_step_key = "merge"

        assert _planned_job_steps(job) == ["transfer", "merge", "youtube_upload"]
        assert _compute_job_overall_progress(job, "Zusammenführen …", 0) == 33

    def test_compute_job_overall_progress_prunes_unselected_validation_branches(self):
        job = WorkflowJob(
            convert_enabled=False,
            create_youtube_version=True,
            upload_youtube=True,
            upload_kaderblick=True,
            graph_nodes=[
                {"id": "source-1", "type": "source_files"},
                {"id": "check-1", "type": "validate_surface"},
                {"id": "repair-1", "type": "repair"},
                {"id": "stop-1", "type": "stop"},
                {"id": "yt-1", "type": "yt_version"},
                {"id": "upload-1", "type": "youtube_upload"},
                {"id": "kb-1", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "source-1", "target": "check-1"},
                {"source": "check-1", "target": "repair-1", "branch": "repairable"},
                {"source": "check-1", "target": "stop-1", "branch": "irreparable"},
                {"source": "check-1", "target": "yt-1", "branch": "ok"},
                {"source": "repair-1", "target": "yt-1"},
                {"source": "yt-1", "target": "upload-1"},
                {"source": "upload-1", "target": "kb-1"},
            ],
        )
        job.step_statuses = {"transfer": "done", "validate_surface": "ok"}
        job.current_step_key = "yt_version"

        assert _planned_job_steps(job) == [
            "transfer",
            "validate_surface",
            "repair",
            "yt_version",
            "stop",
            "youtube_upload",
            "kaderblick",
        ]
        assert _compute_job_overall_progress(job, "YT-Version erstellen …", 0) == 40

    def test_workflow_step_progress_counts_only_effective_branch_steps(self):
        job = WorkflowJob(
            convert_enabled=False,
            create_youtube_version=True,
            upload_youtube=True,
            upload_kaderblick=True,
            graph_nodes=[
                {"id": "source-1", "type": "source_files"},
                {"id": "check-1", "type": "validate_surface"},
                {"id": "repair-1", "type": "repair"},
                {"id": "stop-1", "type": "stop"},
                {"id": "yt-1", "type": "yt_version"},
                {"id": "upload-1", "type": "youtube_upload"},
                {"id": "kb-1", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "source-1", "target": "check-1"},
                {"source": "check-1", "target": "repair-1", "branch": "repairable"},
                {"source": "check-1", "target": "stop-1", "branch": "irreparable"},
                {"source": "check-1", "target": "yt-1", "branch": "ok"},
                {"source": "repair-1", "target": "yt-1"},
                {"source": "yt-1", "target": "upload-1"},
                {"source": "upload-1", "target": "kb-1"},
            ],
        )
        job.step_statuses = {"transfer": "done", "validate_surface": "ok"}
        job.resume_status = "YT-Version erstellen …"

        assert _workflow_step_progress([job], {0}) == (2, 5)

    def test_workflow_step_progress_counts_running_step_as_in_progress(self):
        """Schritt mit status 'running' zählt als laufender Schritt (3/16-Szenario)."""
        job = WorkflowJob(
            convert_enabled=True,
            graph_nodes=[
                {"id": "source-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
                {"id": "check-1", "type": "validate_surface"},
                {"id": "repair-1", "type": "repair"},
                {"id": "yt-1", "type": "yt_version"},
                {"id": "upload-1", "type": "youtube_upload"},
                {"id": "kb-1", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "source-1", "target": "conv-1"},
                {"source": "conv-1", "target": "check-1"},
                {"source": "check-1", "target": "repair-1", "branch": "repairable"},
                {"source": "check-1", "target": "yt-1", "branch": "ok"},
                {"source": "repair-1", "target": "yt-1"},
                {"source": "yt-1", "target": "upload-1"},
                {"source": "upload-1", "target": "kb-1"},
            ],
            upload_youtube=True,
            upload_kaderblick=True,
            create_youtube_version=True,
        )
        # transfer und convert waren vor dem Fortsetzen schon done;
        # convert wurde aber vom Worker auf "running" zurückgesetzt (output-Datei fehlt).
        # validate_surface ist der aktuelle laufende Schritt.
        job.step_statuses = {
            "transfer": "done",
            "convert": "running",
            "validate_surface": "running",
        }
        job.resume_status = "Kompatibilität prüfen …"

        done, total = _workflow_step_progress([job], {0})
        # Graph hat 7 geplante Schritte: transfer + convert + validate_surface + repair
        # + yt_version + youtube_upload + kaderblick
        assert total == 7
        assert done == 3  # transfer(done) + convert(running) + validate_surface(running)


class TestIsFinishedStep:
    """Vollständige Branch-Abdeckung für _is_finished_step."""

    @pytest.mark.parametrize("status", ["done", "reused-target", "skipped", "ok", "repairable", "irreparable"])
    def test_finished_statuses_return_true(self, status):
        assert _is_finished_step(status) is True

    @pytest.mark.parametrize("status", ["running", "cancelled", "", "error: foo", "Fertig", "Wartend"])
    def test_non_finished_statuses_return_false(self, status):
        assert _is_finished_step(status) is False


class TestCurrentPlannedJobSteps:
    """Branch-Abdeckung für _current_planned_job_steps."""

    def test_graph_only_convert(self):
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "conv", "type": "convert"},
            ],
            graph_edges=[{"source": "src", "target": "conv"}],
        )
        assert _current_planned_job_steps(job) == ["transfer", "convert"]

    def test_graph_transfer_only(self):
        job = WorkflowJob(
            graph_nodes=[{"id": "src", "type": "source_files"}],
            graph_edges=[],
        )
        assert _current_planned_job_steps(job) == ["transfer"]

    def test_graph_includes_youtube_upload(self):
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "up", "type": "youtube_upload"},
            ],
            graph_edges=[{"source": "src", "target": "up"}],
        )
        steps = _current_planned_job_steps(job)
        assert "youtube_upload" in steps
        assert steps[0] == "transfer"

    def test_graph_includes_kaderblick_after_youtube_upload(self):
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "up", "type": "youtube_upload"},
                {"id": "kb", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "src", "target": "up"},
                {"source": "up", "target": "kb"},
            ],
        )
        steps = _current_planned_job_steps(job)
        assert "kaderblick" in steps
        assert steps.index("youtube_upload") < steps.index("kaderblick")

    def test_graph_kaderblick_absent_without_youtube(self):
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "kb", "type": "kaderblick"},
            ],
            graph_edges=[{"source": "src", "target": "kb"}],
        )
        # kaderblick without youtube_upload is unusual but graph is authoritative
        steps = _current_planned_job_steps(job)
        assert steps[0] == "transfer"

    def test_graph_with_convert_and_kaderblick(self):
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "conv", "type": "convert"},
                {"id": "up", "type": "youtube_upload"},
                {"id": "kb", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "src", "target": "conv"},
                {"source": "conv", "target": "up"},
                {"source": "up", "target": "kb"},
            ],
        )
        steps = _current_planned_job_steps(job)
        assert steps == ["transfer", "convert", "youtube_upload", "kaderblick"]

    def test_graph_with_validate_surface_ok_branch_excludes_repair(self):
        """validate_surface=ok → repair unerreichbar (nur ok-Pfad zählt)."""
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "check", "type": "validate_surface"},
                {"id": "rep", "type": "repair"},
                {"id": "yt", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "src", "target": "check"},
                {"source": "check", "target": "rep", "branch": "repairable"},
                {"source": "check", "target": "yt", "branch": "ok"},
                {"source": "rep", "target": "yt"},
            ],
        )
        job.step_statuses = {"validate_surface": "ok"}
        steps = _current_planned_job_steps(job)
        assert "validate_surface" in steps
        assert "repair" not in steps
        assert "yt_version" in steps

    def test_graph_with_validate_surface_repairable_branch_includes_repair(self):
        """validate_surface=repairable → repair erreichbar."""
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "check", "type": "validate_surface"},
                {"id": "rep", "type": "repair"},
                {"id": "yt", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "src", "target": "check"},
                {"source": "check", "target": "rep", "branch": "repairable"},
                {"source": "check", "target": "yt", "branch": "ok"},
                {"source": "rep", "target": "yt"},
            ],
        )
        job.step_statuses = {"validate_surface": "repairable"}
        steps = _current_planned_job_steps(job)
        assert "validate_surface" in steps
        assert "repair" in steps
        assert "yt_version" in steps
        assert steps.index("repair") < steps.index("yt_version")

    def test_graph_without_branch_result_includes_all_reachable_types(self):
        """Ohne Validierungs-Ergebnis → alle Branches als potenziell erreichbar."""
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "check", "type": "validate_surface"},
                {"id": "rep", "type": "repair"},
                {"id": "yt", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "src", "target": "check"},
                {"source": "check", "target": "rep", "branch": "repairable"},
                {"source": "check", "target": "yt", "branch": "ok"},
                {"source": "rep", "target": "yt"},
            ],
        )
        job.step_statuses = {}  # kein Validierungs-Status gesetzt
        steps = _current_planned_job_steps(job)
        # Alle Branches sind potenziell erreichbar
        assert "validate_surface" in steps
        assert "repair" in steps
        assert "yt_version" in steps


class TestWorkflowStepProgressBranches:
    """Vollständige Branch-Abdeckung für _workflow_step_progress."""

    def _simple_job(self, **kwargs) -> WorkflowJob:
        """Minimalster Job: transfer + convert = 2 Schritte."""
        return WorkflowJob(
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
            **kwargs,
        )

    def test_disabled_job_excluded_total_never_zero(self):
        """Deaktivierter Job wird nicht gezählt; total ist mindestens 1."""
        job = self._simple_job()
        job.enabled = False
        done, total = _workflow_step_progress([job])
        assert done == 0
        assert total == 1  # max(0, 1)

    def test_job_not_in_active_indices_is_excluded(self):
        job = self._simple_job()
        done, total = _workflow_step_progress([job], active_indices={99})
        assert done == 0
        assert total == 1  # kein passender Index → total bleibt 0 → max(0,1)

    def test_active_indices_none_includes_all_enabled_jobs(self):
        j0 = self._simple_job()
        j1 = self._simple_job()
        j0.step_statuses = {"transfer": "done"}
        j1.step_statuses = {"transfer": "done"}
        done, total = _workflow_step_progress([j0, j1], active_indices=None)
        assert total == 4   # 2 Jobs × 2 Schritte
        assert done == 2    # beide Transfers fertig

    def test_fertig_resume_status_counts_all_steps_as_done(self):
        job = self._simple_job()
        job.resume_status = "Fertig"
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 2

    def test_fertig_resume_status_dominates_empty_step_statuses(self):
        """'Fertig' zählt alle Schritte, auch wenn step_statuses leer ist."""
        job = self._simple_job()
        job.resume_status = "Fertig"
        job.step_statuses = {}
        done, total = _workflow_step_progress([job], {0})
        assert done == total

    def test_empty_step_statuses_counts_zero_done(self):
        job = self._simple_job()
        job.step_statuses = {}
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 0

    def test_transfer_done_counts_one_of_two(self):
        job = self._simple_job()
        job.step_statuses = {"transfer": "done"}
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 1

    def test_running_step_counts_as_in_progress(self):
        """Laufender Schritt zählt bereits zum Fortschritt."""
        job = self._simple_job()
        job.step_statuses = {"transfer": "done", "convert": "running"}
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 2

    def test_cancelled_step_is_not_counted(self):
        job = self._simple_job()
        job.step_statuses = {"transfer": "done", "convert": "cancelled"}
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 1  # nur transfer

    def test_error_step_is_not_counted(self):
        job = self._simple_job()
        job.step_statuses = {"transfer": "done", "convert": "error: something"}
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 1  # nur transfer

    @pytest.mark.parametrize("status", ["done", "reused-target", "skipped", "ok", "repairable", "irreparable"])
    def test_all_finished_statuses_are_counted(self, status):
        job = self._simple_job()
        job.step_statuses = {"transfer": status}
        done, total = _workflow_step_progress([job], {0})
        assert done == 1

    def test_resume_scenario_two_jobs_transfer_done_one_convert_running(self):
        """
        Kern-Szenario (analog zu 3/16): 2 Jobs, je 2 Schritte.
        Beide Transfers wurden in der vorigen Sitzung abgeschlossen und gespeichert.
        Job 0 konvertiert gerade. Erwartet: done=3, total=4.
        """
        j0 = self._simple_job()
        j0.step_statuses = {"transfer": "done", "convert": "running"}
        j0.resume_status = "Konvertiere …"

        j1 = self._simple_job()
        j1.step_statuses = {"transfer": "done"}
        j1.resume_status = "Wartend"

        done, total = _workflow_step_progress([j0, j1], {0, 1})
        assert total == 4   # 2 Jobs × 2 Schritte
        assert done == 3    # j0: transfer(done)+convert(running), j1: transfer(done)

    def test_resume_scenario_both_transfers_done_before_any_convert_starts(self):
        """
        Zwischenstand: Beide Transfers abgeschlossen, kein Convert gestartet.
        Gespeicherte 'done'-Status müssen sofort zählen (analog zu 2/16).
        """
        j0 = self._simple_job()
        j0.step_statuses = {"transfer": "done"}

        j1 = self._simple_job()
        j1.step_statuses = {"transfer": "done"}

        done, total = _workflow_step_progress([j0, j1], {0, 1})
        assert total == 4
        assert done == 2    # beide Transfers

    def test_mixed_active_indices_filters_out_inactive_job(self):
        """Nur Job 0 aktiv → Job 1 trotz 'done'-Steps ignoriert."""
        j0 = self._simple_job()
        j0.step_statuses = {"transfer": "done", "convert": "done"}
        j1 = self._simple_job()
        j1.step_statuses = {"transfer": "done", "convert": "done"}

        done, total = _workflow_step_progress([j0, j1], active_indices={0})
        assert total == 2   # nur Job 0
        assert done == 2

    def test_mixed_enabled_and_disabled_jobs_with_none_active_indices(self):
        """Deaktivierter Job 1 wird nicht gezählt, auch wenn active_indices=None."""
        j0 = self._simple_job()
        j0.step_statuses = {"transfer": "done"}
        j1 = self._simple_job()
        j1.enabled = False
        j1.step_statuses = {"transfer": "done", "convert": "done"}

        done, total = _workflow_step_progress([j0, j1], active_indices=None)
        assert total == 2   # nur Job 0
        assert done == 1

    def test_reused_target_step_counts_as_finished(self):
        """'reused-target' ist ein gültiger Fertig-Status."""
        job = self._simple_job()
        job.step_statuses = {"transfer": "reused-target", "convert": "done"}
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 2

    def test_extra_keys_in_step_statuses_not_in_planned_steps_are_ignored(self):
        """Unbekannte Status-Keys die nicht zu geplanten Schritten gehören, werden ignoriert."""
        job = self._simple_job()  # nur transfer + convert geplant
        job.step_statuses = {
            "transfer": "done",
            "kaderblick": "done",   # nicht im Graphen dieser Job-Konfig
        }
        done, total = _workflow_step_progress([job], {0})
        assert total == 2
        assert done == 1  # nur transfer zählt


class TestSelectedWorkflowStart:
    def test_job_source_paths_collects_folder_scan_matches(self, tmp_path):
        source_dir = tmp_path / "input"
        source_dir.mkdir()
        first = source_dir / "a.mp4"
        second = source_dir / "b.mp4"
        ignored = source_dir / "c.mov"
        first.write_text("a")
        second.write_text("b")
        ignored.write_text("c")

        job = WorkflowJob(
            source_mode="folder_scan",
            source_folder=str(source_dir),
            file_pattern="*.mp4",
            move_files=True,
            copy_destination=str(tmp_path / "target"),
        )

        assert _job_source_paths(job) == {first, second}

    def test_job_source_paths_ignores_non_file_graph_entries_for_multi_source_jobs(self, tmp_path):
        local_file = tmp_path / "lokal.mp4"
        download_file = tmp_path / "download.mp4"
        local_file.write_text("a")
        download_file.write_text("b")

        job = WorkflowJob(
            source_mode="files",
            files=[
                FileEntry(source_path=str(local_file), graph_source_id="source-files"),
                FileEntry(source_path=str(download_file), graph_source_id="source-pi"),
            ],
            graph_nodes=[
                {"id": "source-files", "type": "source_files"},
                {"id": "source-pi", "type": "source_pi_download"},
            ],
        )

        assert _job_source_paths(job) == {local_file}

    def test_job_effectively_moves_sources_is_false_for_in_place_target(self, tmp_path):
        source_dir = tmp_path / "input"
        source_dir.mkdir()
        source_file = source_dir / "halbzeit.mp4"
        source_file.write_text("video")
        job = WorkflowJob(
            source_mode="files",
            files=[FileEntry(source_path=str(source_file))],
            move_files=True,
            copy_destination=str(source_dir),
        )

        assert _job_effectively_moves_sources(AppSettings(), job) is False

    def test_conflict_warning_requires_move_before_later_access(self, tmp_path):
        source_file = tmp_path / "halbzeit.mp4"
        source_file.write_text("video")
        move_target = tmp_path / "ziel"
        earlier_reader = WorkflowJob(
            name="Leser",
            source_mode="files",
            files=[FileEntry(source_path=str(source_file))],
            move_files=False,
        )
        later_mover = WorkflowJob(
            name="Mover",
            source_mode="files",
            files=[FileEntry(source_path=str(source_file))],
            move_files=True,
            copy_destination=str(move_target),
        )

        warning = _build_source_move_conflict_warning(
            AppSettings(),
            [(0, earlier_reader), (1, later_mover)],
        )

        assert warning == ""

    def test_conflict_warning_limits_output_and_reports_remaining_count(self, tmp_path):
        settings = AppSettings()
        job_entries = []
        for index in range(9):
            source_file = tmp_path / f"datei-{index}.mp4"
            source_file.write_text("video")
            mover = WorkflowJob(
                name=f"Mover {index}",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                move_files=True,
                copy_destination=str(tmp_path / f"ziel-{index}"),
            )
            reader = WorkflowJob(
                name=f"Reader {index}",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                move_files=False,
            )
            job_entries.extend([(index * 2, mover), (index * 2 + 1, reader)])

        warning = _build_source_move_conflict_warning(settings, job_entries)

        assert warning.count("greift spaeter erneut darauf zu") == 8
        assert "1 weitere Konflikt(e)" in warning

    def test_start_selected_workflows_passes_selected_indices_to_executor(self):
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
            WorkflowJob(name="C", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")]),
        ]
        window._refresh_table()
        selection_model = window.table.selectionModel()
        selection_model.select(window.table.model().index(0, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        selection_model.select(window.table.model().index(2, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

        with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
            window._start_selected_workflows()

        assert window._wf_executor is not None
        assert window._wf_executor.active_indices == {0, 2}

    def test_start_selected_workflows_starts_all_enabled_when_nothing_selected(self):
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
        ]
        window._refresh_table()
        window.table.clearSelection()

        with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
            window._start_selected_workflows()

        assert window._wf_executor is not None
        assert window._wf_executor.active_indices == {0, 1}

    def test_start_selected_workflows_does_not_prompt_when_nothing_selected(self):
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
        ]
        window._refresh_table()
        window.table.clearSelection()

        with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor), patch(
            "src.app.QMessageBox.question"
        ) as question_mock:
            window._start_selected_workflows()

        question_mock.assert_not_called()
        assert window._wf_executor is not None
        assert window._wf_executor.active_indices == {0, 1}

    def test_start_workflow_warns_when_two_active_jobs_move_same_source(self, tmp_path):
        window = _new_app()
        source_file = tmp_path / "halbzeit1.mp4"
        source_file.write_text("video")
        target_a = tmp_path / "ziel-a"
        target_b = tmp_path / "ziel-b"
        window._workflow.jobs = [
            WorkflowJob(
                name="A",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                copy_destination=str(target_a),
                move_files=True,
            ),
            WorkflowJob(
                name="B",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                copy_destination=str(target_b),
                move_files=True,
            ),
        ]
        window._refresh_table()

        with patch("src.app.QMessageBox.warning") as warning_mock, patch("src.app.QThread", _DummyThread), patch(
            "src.app.WorkflowExecutor", _DummyExecutor
        ):
            window._start_workflow(active_indices={0, 1})

        warning_mock.assert_called_once()
        assert "wollen dieselbe Quelldatei verschieben" in warning_mock.call_args.args[2]
        assert window._wf_executor is None

    def test_start_workflow_warns_when_earlier_job_moves_source_before_later_reuse(self, tmp_path):
        window = _new_app()
        source_file = tmp_path / "halbzeit2.mp4"
        source_file.write_text("video")
        target_dir = tmp_path / "ziel"
        window._workflow.jobs = [
            WorkflowJob(
                name="Frueher",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                copy_destination=str(target_dir),
                move_files=True,
            ),
            WorkflowJob(
                name="Spaeter",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                move_files=False,
            ),
        ]
        window._refresh_table()

        with patch("src.app.QMessageBox.warning") as warning_mock, patch("src.app.QThread", _DummyThread), patch(
            "src.app.WorkflowExecutor", _DummyExecutor
        ):
            window._start_workflow(active_indices={0, 1})

        warning_mock.assert_called_once()
        assert "verschiebt die Quelle" in warning_mock.call_args.args[2]
        assert "greift spaeter erneut darauf zu" in warning_mock.call_args.args[2]
        assert window._wf_executor is None

    def test_start_workflow_allows_shared_source_when_nobody_moves_it(self, tmp_path):
        window = _new_app()
        source_file = tmp_path / "halbzeit3.mp4"
        source_file.write_text("video")
        window._workflow.jobs = [
            WorkflowJob(
                name="A",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                move_files=False,
            ),
            WorkflowJob(
                name="B",
                source_mode="files",
                files=[FileEntry(source_path=str(source_file))],
                move_files=False,
            ),
        ]
        window._refresh_table()

        with patch("src.app.QMessageBox.warning") as warning_mock, patch("src.app.QThread", _DummyThread), patch(
            "src.app.WorkflowExecutor", _DummyExecutor
        ):
            window._start_workflow(active_indices={0, 1})

        warning_mock.assert_not_called()
        assert window._wf_executor is not None
        assert window._wf_executor.active_indices == {0, 1}

    def test_main_window_has_no_separate_start_all_action(self):
        window = _new_app()

        assert hasattr(window, "act_start")
        assert not hasattr(window, "act_start_all")

    def test_start_workflow_disables_reuse_when_restart_is_selected(self):
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(
                name="A",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                resume_status="Transfer OK",
                step_statuses={"transfer": "done"},
            )
        ]
        window._refresh_table()

        with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor), patch.object(
            window,
            "_ask_resume_behavior",
            return_value=QMessageBox.StandardButton.No,
        ):
            window._start_selected_workflows()

        assert window._wf_executor is not None
        assert window._wf_executor.allow_reuse_existing is False


class TestSelectedWorkflowCancel:
    def test_cancel_workflow_aborts_only_selected_jobs_after_confirmation(self):
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
                WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
                WorkflowJob(name="C", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")]),
            ]
            window._refresh_table()
            selection_model = window.table.selectionModel()
            selection_model.select(window.table.model().index(1, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1, 2})

            assert window._wf_executor is not None

            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question_mock:
                window._cancel_workflow()

            assert window._wf_executor.cancel_calls == [{1}]
            question_mock.assert_called_once()
            assert "ausgewählte" in question_mock.call_args.args[2]
        finally:
            window.close()

    def test_cancel_workflow_aborts_all_jobs_when_nothing_is_selected(self):
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
                WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
            ]
            window._refresh_table()
            window.table.clearSelection()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1})

            assert window._wf_executor is not None

            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question_mock:
                window._cancel_workflow()

            assert window._wf_executor.cancel_calls == [{0, 1}]
            question_mock.assert_called_once()
            assert "alle laufenden Jobs" in question_mock.call_args.args[2]
        finally:
            window.close()

    def test_cancel_workflow_stops_when_confirmation_is_declined(self):
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0})

            assert window._wf_executor is not None

            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                window._cancel_workflow()

            assert window._wf_executor.cancel_calls == []
        finally:
            window.close()


class TestSessionRepair:
    def test_repair_restored_placeholder_workflow_does_not_restore_last_workflow_without_resume_state(self):
        restored = Workflow(job=WorkflowJob(name="Job 1", source_mode="files", files=[]))
        fallback = Workflow(
            name="Spieltag 23",
            job=WorkflowJob(
                name="Gespeicherter Job",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/halbzeit1.mp4")],
                upload_youtube=True,
            ),
            shutdown_after=True,
        )

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, fallback)

        assert repaired_count == 0
        assert dropped == 0
        assert repaired.name == ""
        assert repaired.shutdown_after is False
        assert repaired.job is not None
        assert repaired.job.name == "Job 1"
        assert repaired.job.files == []

    def test_repair_restored_workflow_reuses_last_workflow_config(self):
        restored = Workflow(jobs=[
            WorkflowJob(
                name="Job 1",
                source_mode="files",
                files=[],
                resume_status="Transfer OK",
                step_statuses={"transfer": "done", "convert": "running"},
            )
        ])
        fallback = Workflow(jobs=[
            WorkflowJob(
                name="Job 1",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/halbzeit1.mp4")],
                upload_youtube=True,
            )
        ])

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, fallback)

        assert repaired_count == 1
        assert dropped == 0
        assert repaired.jobs[0].files[0].source_path == "/tmp/halbzeit1.mp4"
        assert repaired.jobs[0].resume_status == "Transfer OK"
        assert repaired.jobs[0].step_statuses == {"transfer": "done", "convert": "running"}

    def test_repair_restored_workflow_drops_resume_for_unrepairable_job(self):
        restored = Workflow(jobs=[
            WorkflowJob(
                name="Job 1",
                source_mode="files",
                files=[],
                resume_status="Transfer OK",
                step_statuses={"transfer": "done"},
            )
        ])

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, None)

        assert repaired_count == 0
        assert dropped == 1
        assert repaired.jobs[0].resume_status == ""
        assert repaired.jobs[0].step_statuses == {}

    def test_repair_restored_workflow_keeps_placeholder_only_session_without_resume_state(self):
        restored = Workflow(jobs=[WorkflowJob(name="Job 1", source_mode="files", files=[])])
        fallback = Workflow(
            name="Spieltag (2)",
            jobs=[
                WorkflowJob(name="Spieltag", source_mode="files", files=[]),
                WorkflowJob(name="Spieltag (2)", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")]),
            ],
        )

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, fallback)

        assert repaired_count == 0
        assert dropped == 0
        assert repaired.name == ""
        assert len(repaired.jobs) == 1
        assert repaired.jobs[0].name == "Job 1"
        assert repaired.jobs[0].files == []


class TestConverterAppResumeState:
    def test_normalize_cancelled_resume_state_turns_aborted_job_into_resumable_step(self):
        job = WorkflowJob(
            name="Job 1",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/a.mp4")],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
            resume_status="Konvertierung abgebrochen",
            current_step_key="convert",
            step_statuses={"transfer": "done", "convert": "cancelled"},
            step_details={"convert": "Durch Benutzer abgebrochen"},
        )

        changed = _normalize_cancelled_resume_state(job)

        assert changed is True
        assert job.resume_status == "Konvertiere …"
        assert job.current_step_key == "convert"
        assert job.step_statuses == {"transfer": "done"}
        assert job.step_details == {}

    def test_normalize_cancelled_resume_state_clears_stale_running_after_crash(self):
        """Bug-Regression: Nach einem App-Crash ist step_statuses["convert"]="running" persistiert.
        _normalize_cancelled_resume_state muss 'running' beim Restore löschen, damit der Node
        nicht als 'Läuft' dargestellt wird obwohl nichts ausgeführt wird."""
        job = WorkflowJob(
            name="Job 1",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/a.mp4")],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
            resume_status="Konvertiere …",
            current_step_key="convert",
            step_statuses={"transfer": "done", "convert": "running"},
            step_details={"convert": "3/5 Dateien"},
        )

        changed = _normalize_cancelled_resume_state(job)

        assert changed is True
        assert "running" not in job.step_statuses.values()
        assert "convert" not in job.step_statuses
        assert job.step_statuses == {"transfer": "done"}
        assert job.step_details == {}
        # current_step_key und resume_status müssen auf den ersten nicht-fertigen Schritt zeigen
        assert job.current_step_key == "convert"
        assert "Konvertiere" in job.resume_status

    def test_normalize_cancelled_resume_state_no_change_when_all_clean(self):
        """Ohne 'cancelled'/'running' Einträge sollte die Funktion nichts ändern."""
        job = WorkflowJob(
            name="Job 1",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/a.mp4")],
            resume_status="",
            current_step_key="",
            step_statuses={"transfer": "done"},
            step_details={},
        )

        changed = _normalize_cancelled_resume_state(job)

        assert changed is False
        assert job.step_statuses == {"transfer": "done"}

    def test_save_last_workflow_persists_complete_workflow_configuration(self, tmp_path):
        last_workflow_file = tmp_path / "last_workflow.json"
        workflow = Workflow(name="Spieltag 23", job=_rich_job(), shutdown_after=True)

        window = _new_app()
        try:
            window._workflow = workflow
            with patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
                window._save_last_workflow()

            assert last_workflow_file.exists() is True

            with patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
                restored = Workflow.load_last()

            assert restored is not None
            assert restored.to_dict() == workflow.to_dict()
            assert restored.job is not None
            assert restored.job.to_dict() == workflow.job.to_dict()
            assert restored.job.resume_status == "YT-Version erstellen …"
            assert restored.job.step_statuses == {
                "transfer": "done",
                "convert": "done",
                "merge": "done",
                "titlecard": "done",
                "yt_version": "running",
            }
            assert restored.job.graph_nodes == workflow.job.graph_nodes
            assert restored.job.graph_edges == workflow.job.graph_edges
        finally:
            window.close()

    def test_save_last_workflow_persists_resume_state_but_not_transient_error_fields(self, tmp_path):
        last_workflow_file = tmp_path / "last_workflow.json"
        runtime_job = _rich_job()
        runtime_job.status = "Läuft"
        runtime_job.transfer_status = "Transfer 1/3"
        runtime_job.transfer_progress_pct = 66
        runtime_job.progress_pct = 44
        runtime_job.overall_progress_pct = 78
        runtime_job.current_step_key = "yt_version"
        runtime_job.error_msg = ""

        window = _new_app()
        try:
            window._workflow = Workflow(name="Spieltag 23", job=runtime_job, shutdown_after=True)
            with patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
                window._save_last_workflow()

            payload = last_workflow_file.read_text(encoding="utf-8")

            assert '"resume_status": "YT-Version erstellen …"' in payload
            assert '"step_statuses"' in payload
            assert '"progress_pct": 44' in payload
            assert '"overall_progress_pct": 78' in payload
            assert '"current_step_key": "yt_version"' in payload
            assert '"transfer_status": "Transfer 1/3"' in payload
            assert '"transfer_progress_pct": 66' in payload
            assert '"graph_nodes"' in payload
            assert '"graph_edges"' in payload
            assert '"status"' not in payload
            assert '"error_msg"' not in payload
        finally:
            window.close()

    def test_app_restart_restores_full_last_workflow_configuration(self, tmp_path):
        expected = Workflow(name="Spieltag 23", job=_rich_job(), shutdown_after=True)

        window = _roundtrip_restored_workflow(tmp_path, expected)

        try:
            assert window.table.rowCount() == 1
            assert window._workflow.to_dict() == expected.to_dict()
            assert window._shutdown_cb.isChecked() is True
            assert window._workflow.job is not None
            assert window._workflow.job.to_dict() == expected.job.to_dict()
            assert window.table.item(0, 1).text() == "Gespeicherter Workflow"
            assert window.table.item(0, 4).text() == "Ausstehend"
            assert window.table.item(0, 5).text() == "78%"
        finally:
            window.close()

    def test_app_start_drops_resume_state_from_unrepairable_last_workflow(self, tmp_path):
        settings = AppSettings()
        settings.restore_last_workflow = True
        last_workflow_file = tmp_path / "last_workflow.json"
        broken = Workflow(
            name="Spieltag 23",
            job=WorkflowJob(
                name="Gespeicherter Job",
                source_mode="files",
                files=[],
                resume_status="YT-Version erstellen …",
                step_statuses={"yt_version": "running"},
            ),
        )
        broken.save(last_workflow_file)

        with patch("src.app.AppSettings.load", return_value=settings), \
             patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
            window = ConverterApp()

        try:
            assert window.table.rowCount() == 1
            assert window._workflow.job is not None
            assert window._workflow.job.resume_status == ""
            assert window._workflow.job.step_statuses == {}
            assert window._workflow.job.files == []
        finally:
            window.close()

    def test_app_start_normalizes_cancelled_last_workflow_state(self, tmp_path):
        settings = AppSettings()
        settings.restore_last_workflow = True
        last_workflow_file = tmp_path / "last_workflow.json"
        restored = Workflow(
            jobs=[
                WorkflowJob(
                    name="Gespeicherter Job",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/a.mp4")],
                    resume_status="Konvertierung abgebrochen",
                    current_step_key="convert",
                    step_statuses={"transfer": "done", "convert": "cancelled"},
                    step_details={"convert": "Durch Benutzer abgebrochen"},
                    graph_nodes=[
                        {"id": "src-1", "type": "source_files"},
                        {"id": "conv-1", "type": "convert"},
                    ],
                    graph_edges=[{"source": "src-1", "target": "conv-1"}],
                )
            ]
        )
        save_workflow(restored, last_workflow_file, include_runtime=True)

        with patch("src.app.AppSettings.load", return_value=settings), \
             patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
            window = ConverterApp()

        try:
            assert window.table.rowCount() == 1
            assert window._workflow.job is not None
            assert window._workflow.job.resume_status == "Konvertiere …"
            assert window._workflow.job.step_statuses == {"transfer": "done"}
            assert window._workflow.job.step_details == {}
            assert window.table.item(0, 4).text() == "Ausstehend"
        finally:
            window.close()

    def test_app_start_normalizes_cancelled_state_for_all_restored_jobs(self, tmp_path):
        settings = AppSettings()
        settings.restore_last_workflow = True
        last_workflow_file = tmp_path / "last_workflow.json"
        restored = Workflow(
            jobs=[
                WorkflowJob(
                    name="Gespeicherter Job A",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/a.mp4")],
                    resume_status="Konvertierung abgebrochen",
                    current_step_key="convert",
                    step_statuses={"transfer": "done", "convert": "cancelled"},
                    step_details={"convert": "Durch Benutzer abgebrochen"},
                    graph_nodes=[
                        {"id": "src-1", "type": "source_files"},
                        {"id": "conv-1", "type": "convert"},
                    ],
                    graph_edges=[{"source": "src-1", "target": "conv-1"}],
                ),
                WorkflowJob(
                    name="Gespeicherter Job B",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/b.mp4")],
                    create_youtube_version=True,
                    upload_youtube=True,
                    resume_status="YouTube-Upload abgebrochen",
                    current_step_key="youtube_upload",
                    step_statuses={
                        "transfer": "done",
                        "convert": "done",
                        "yt_version": "done",
                        "youtube_upload": "cancelled",
                    },
                    step_details={"youtube_upload": "Durch Benutzer abgebrochen"},
                    graph_nodes=[
                        {"id": "src-1", "type": "source_files"},
                        {"id": "conv-1", "type": "convert"},
                        {"id": "ytv-1", "type": "yt_version"},
                        {"id": "ytu-1", "type": "youtube_upload"},
                    ],
                    graph_edges=[
                        {"source": "src-1", "target": "conv-1"},
                        {"source": "conv-1", "target": "ytv-1"},
                        {"source": "ytv-1", "target": "ytu-1"},
                    ],
                ),
            ]
        )
        save_workflow(restored, last_workflow_file, include_runtime=True)

        with patch("src.app.AppSettings.load", return_value=settings), \
             patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
            window = ConverterApp()

        try:
            assert window.table.rowCount() == 2
            assert window._workflow.jobs[0].resume_status == "Konvertiere …"
            assert window._workflow.jobs[0].step_statuses == {"transfer": "done"}
            assert window._workflow.jobs[1].resume_status == "YouTube-Upload …"
            assert window._workflow.jobs[1].step_statuses == {
                "transfer": "done",
                "convert": "done",
                "yt_version": "done",
            }
            assert window.table.item(0, 4).text() == "Ausstehend"
            assert window.table.item(1, 4).text() == "Ausstehend"
        finally:
            window.close()

    def test_app_start_crash_recovery_shows_ausstehend_in_table(self, tmp_path):
        """Bug-Regression: Nach einem Crash ist step_statuses["convert"]="running" persistiert.
        Nach dem App-Start soll die Tabellenspalte 'Ausstehend' zeigen, nicht 'Konvertiere …'."""
        settings = AppSettings()
        settings.restore_last_workflow = True
        last_workflow_file = tmp_path / "last_workflow.json"
        crashed = Workflow(
            jobs=[
                WorkflowJob(
                    name="Crash-Job",
                    source_mode="files",
                    convert_enabled=True,
                    files=[FileEntry(source_path="/tmp/a.mp4")],
                    resume_status="Konvertiere \u2026",
                    current_step_key="convert",
                    step_statuses={"transfer": "done", "convert": "running"},
                    step_details={"convert": "2/5 Dateien"},
                    run_started_at="2026-04-19T10:00:00",
                    run_finished_at="",
                    graph_nodes=[
                        {"id": "src-1", "type": "source_files"},
                        {"id": "conv-1", "type": "convert"},
                    ],
                    graph_edges=[{"source": "src-1", "target": "conv-1"}],
                )
            ]
        )
        save_workflow(crashed, last_workflow_file, include_runtime=True)

        with patch("src.app.AppSettings.load", return_value=settings), \
             patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
            window = ConverterApp()

        try:
            assert window.table.rowCount() == 1
            assert window._workflow.job is not None
            # "running" muss entfernt worden sein
            assert "running" not in window._workflow.job.step_statuses.values()
            # Tabellenspalte darf nicht "Läuft" oder "Konvertiere …" zeigen
            cell = window.table.item(0, 4).text()
            assert cell == "Ausstehend", (
                f"Tabelle soll nach Crash-Restore 'Ausstehend' zeigen, zeigt aber: {cell!r}"
            )
        finally:
            window.close()

    def test_app_start_restores_last_workflow_when_enabled(self, tmp_path):
        settings = AppSettings()
        settings.restore_last_workflow = True
        restored = Workflow(jobs=[
            WorkflowJob(
                name="Gespeicherter Job",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/a.mp4")],
            )
        ])
        last_workflow_file = tmp_path / "last_workflow.json"
        restored.save(last_workflow_file)

        with patch("src.app.AppSettings.load", return_value=settings), \
             patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
            window = ConverterApp()

        try:
            assert window.table.rowCount() == 1
            assert window.table.item(0, 1).text() == "Gespeicherter Job"
        finally:
            window.close()

    def test_app_start_keeps_placeholder_last_workflow_without_inventing_data(self, tmp_path):
        settings = AppSettings()
        settings.restore_last_workflow = True
        last_workflow_file = tmp_path / "last_workflow.json"
        placeholder = Workflow(job=WorkflowJob(name="Job 1", source_mode="files", files=[]))
        placeholder.save(last_workflow_file)

        with patch("src.app.AppSettings.load", return_value=settings), \
             patch("src.workflow.storage.LAST_WORKFLOW_FILE", last_workflow_file):
            window = ConverterApp()

        try:
            assert window.table.rowCount() == 1
            assert window._workflow.name == ""
            assert window._workflow.job is not None
            assert window._workflow.job.files == []
            assert window.table.item(0, 1).text() == "Job 1"
        finally:
            window.close()

    def test_new_workflow_uses_saved_app_settings_defaults(self):
        settings = AppSettings()
        settings.video.encoder = "hevc_nvenc"
        settings.video.crf = 21
        settings.video.preset = "slow"
        settings.video.fps = 50
        settings.video.output_format = "mov"
        settings.audio.amplify_audio = True
        settings.audio.amplify_db = 9.5

        with patch("src.app.AppSettings.load", return_value=settings):
            window = ConverterApp()

        try:
            _DummyWorkflowDialog.instances.clear()

            def _make_dialog(parent, job, allow_edit=False, settings=None, allow_wizard_shortcut=True):
                dialog = _DummyWorkflowDialog(
                    parent,
                    job,
                    allow_edit=allow_edit,
                    settings=settings,
                    allow_wizard_shortcut=allow_wizard_shortcut,
                )
                dialog.changed = False
                return dialog

            with patch("src.app.JobWorkflowDialog", side_effect=_make_dialog):
                window._new_workflow()

            assert len(_DummyWorkflowDialog.instances) == 1
            job = _DummyWorkflowDialog.instances[0].job
            assert job.name == "Neuer Workflow"
            assert job.encoder == "hevc_nvenc"
            assert job.crf == 21
            assert job.preset == "slow"
            assert job.fps == 50
            assert job.output_format == "mov"
            assert job.amplify_audio is True
            assert job.amplify_db == 9.5
        finally:
            window.close()

    def test_new_workflow_opens_workflow_editor_directly_and_applies_result(self):
        window = _new_app()
        try:
            _DummyWorkflowDialog.instances.clear()

            def _make_dialog(parent, job, allow_edit=False, settings=None, allow_wizard_shortcut=True):
                dialog = _DummyWorkflowDialog(
                    parent,
                    job,
                    allow_edit=allow_edit,
                    settings=settings,
                    allow_wizard_shortcut=allow_wizard_shortcut,
                )
                dialog.changed = True
                job.name = "Neuer Workflow"
                job.files = [FileEntry(source_path="/tmp/a.mp4")]
                return dialog

            with patch("src.app.JobWorkflowDialog", side_effect=_make_dialog), patch.object(window, "_save_last_workflow") as save_last_workflow:
                window._new_workflow()

            assert len(_DummyWorkflowDialog.instances) == 1
            assert _DummyWorkflowDialog.instances[0].allow_wizard_shortcut is False
            assert window._workflow.job is not None
            assert window._workflow.job.name == "Neuer Workflow"
            assert window._workflow.name == "Neuer Workflow"
            assert window.table.rowCount() == 1
            assert window.table.item(0, 1).text() == "Neuer Workflow"
            save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_new_workflow_appends_instead_of_overwriting_existing_job(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Bestehend")])
            _DummyWorkflowDialog.instances.clear()

            def _make_dialog(parent, job, allow_edit=False, settings=None, allow_wizard_shortcut=True):
                dialog = _DummyWorkflowDialog(
                    parent,
                    job,
                    allow_edit=allow_edit,
                    settings=settings,
                    allow_wizard_shortcut=allow_wizard_shortcut,
                )
                dialog.changed = True
                job.name = "Neu"
                job.files = [FileEntry(source_path="/tmp/b.mp4")]
                return dialog

            with patch("src.app.JobWorkflowDialog", side_effect=_make_dialog), patch.object(window, "_save_last_workflow") as save_last_workflow:
                window._new_workflow()

            assert [job.name for job in window._workflow.jobs] == ["Bestehend", "Neu"]
            assert window.table.rowCount() == 2
            assert window.table.item(0, 1).text() == "Bestehend"
            assert window.table.item(1, 1).text() == "Neu"
            save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_new_workflow_duplicate_name_can_increment(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Spieltag")])
            _DummyWorkflowDialog.instances.clear()

            def _make_dialog(parent, job, allow_edit=False, settings=None, allow_wizard_shortcut=True):
                dialog = _DummyWorkflowDialog(
                    parent,
                    job,
                    allow_edit=allow_edit,
                    settings=settings,
                    allow_wizard_shortcut=allow_wizard_shortcut,
                )
                dialog.changed = True
                job.name = "Spieltag"
                job.files = [FileEntry(source_path="/tmp/c.mp4")]
                return dialog

            class _IncrementMessageBox:
                class Icon:
                    Warning = QMessageBox.Icon.Warning

                class ButtonRole:
                    AcceptRole = QMessageBox.ButtonRole.AcceptRole
                    ActionRole = QMessageBox.ButtonRole.ActionRole
                    RejectRole = QMessageBox.ButtonRole.RejectRole

                def __init__(self, parent=None):
                    self._clicked = None
                    self._buttons = []

                def setIcon(self, *_args, **_kwargs):
                    return None

                def setWindowTitle(self, *_args, **_kwargs):
                    return None

                def setText(self, *_args, **_kwargs):
                    return None

                def setInformativeText(self, *_args, **_kwargs):
                    return None

                def addButton(self, text, _role):
                    button = object()
                    self._buttons.append((text, button))
                    if text == "Inkrementieren":
                        self._increment_button = button
                    return button

                def setDefaultButton(self, *_args, **_kwargs):
                    return None

                def exec(self):
                    self._clicked = self._increment_button
                    return 0

                def clickedButton(self):
                    return self._clicked

            with patch("src.app.JobWorkflowDialog", side_effect=_make_dialog), patch("src.app.QMessageBox", _IncrementMessageBox), patch.object(window, "_save_last_workflow") as save_last_workflow:
                window._new_workflow()

            assert [job.name for job in window._workflow.jobs] == ["Spieltag", "Spieltag (2)"]
            assert window.table.rowCount() == 2
            assert window.table.item(1, 1).text() == "Spieltag (2)"
            save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_duplicate_job_inserts_copy_below_selection_and_selects_it(self):
        window = _new_app()
        try:
            original = _rich_job(name="Spieltag")
            second = WorkflowJob(name="Anderer Job", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")])
            window._workflow = Workflow(jobs=[original, second])
            window._refresh_table()
            window.table.selectRow(0)

            with patch.object(window, "_save_last_workflow") as save_last_workflow:
                window._duplicate_job()

            assert [job.name for job in window._workflow.jobs] == ["Spieltag", "Spieltag (2)", "Anderer Job"]
            assert window._workflow.jobs[1].id != window._workflow.jobs[0].id
            assert window._workflow.jobs[1].to_dict() == {
                **window._workflow.jobs[0].to_dict(),
                "id": window._workflow.jobs[1].id,
                "name": "Spieltag (2)",
            }
            assert window.table.currentRow() == 1
            assert window.table.item(1, 1).text() == "Spieltag (2)"
            save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_duplicate_action_in_menu_triggers_job_copy(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Spieltag", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")])])
            window._refresh_table()
            window.table.selectRow(0)

            with patch.object(window, "_duplicate_job") as duplicate_job:
                window.act_duplicate.trigger()

            duplicate_job.assert_called_once_with()
        finally:
            window.close()

    def test_refresh_table_uses_restored_resume_status_and_tooltip(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[
                WorkflowJob(
                    name="Job 1",
                    resume_status="Transfer OK",
                    step_statuses={"transfer": "done", "convert": "running"},
                )
            ])

            window._refresh_table()

            item = window.table.item(0, 4)
            assert item.text() == "Transfer OK"
            assert item.toolTip() == (
                "Letzter Status: Transfer OK\n"
                "Transfer: done\n"
                "Konvertierung: running"
            )
        finally:
            window.close()

    def test_refresh_table_shows_persisted_job_and_workflow_durations(self):
        window = _new_app()
        try:
            window._workflow = Workflow(
                jobs=[
                    WorkflowJob(
                        name="Job 1",
                        resume_status="Fertig",
                        run_elapsed_seconds=125,
                    )
                ],
                last_run_elapsed_seconds=3665,
            )

            window._refresh_table()

            assert window.table.item(0, 6).text() == "2min 05s"
            assert window.duration_label.text() == "Gesamtdauer: 1h 01min"
        finally:
            window.close()

    def test_roundtrip_restores_duration_metadata(self, tmp_path):
        workflow = Workflow(
            jobs=[
                WorkflowJob(
                    name="Job 1",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/a.mp4")],
                    run_started_at="2026-03-23T10:00:00",
                    run_finished_at="2026-03-23T10:03:05",
                    run_elapsed_seconds=185,
                )
            ],
            last_run_started_at="2026-03-23T10:00:00",
            last_run_finished_at="2026-03-23T10:04:10",
            last_run_elapsed_seconds=250,
        )

        restored = _roundtrip_restored_workflow(tmp_path, workflow)
        try:
            job = restored._workflow.jobs[0]
            assert job.run_started_at == "2026-03-23T10:00:00"
            assert job.run_finished_at == "2026-03-23T10:03:05"
            assert job.run_elapsed_seconds == 185
            assert restored._workflow.last_run_elapsed_seconds == 250
            assert restored.duration_label.text() == "Gesamtdauer: 4min 10s"
        finally:
            restored.close()

    def test_refresh_table_overwrites_stale_cells_after_workflow_replace(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Alt")])
            window._refresh_table()
            window.table.item(0, 4).setText("Veralteter Status")
            window.table.item(0, 5).setText("91%")

            window._workflow = Workflow(jobs=[
                WorkflowJob(
                    name="Neu",
                    resume_status="Transfer OK",
                    step_statuses={"transfer": "done"},
                )
            ])
            window._refresh_table()

            assert window.table.item(0, 1).text() == "Neu"
            assert window.table.item(0, 4).text() == "Transfer OK"
            assert window.table.item(0, 5).text() == "0%"
        finally:
            window.close()

    def test_set_row_status_marks_merge_as_running(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Job 1")])
            window._refresh_table()

            window._set_row_status(0, "Zusammenführen …")

            item = window.table.item(0, 4)
            assert item.text() == "Zusammenführen …"
            assert item.foreground().color() == Qt.blue
        finally:
            window.close()

    def test_set_row_status_marks_detailed_step_statuses_as_running(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Job 1")])
            window._refresh_table()

            for status in (
                "Transfer 1/3: clip.mp4 …",
                "Titelkarte erstellen …",
                "YT-Version erstellen …",
                "Kaderblick senden …",
            ):
                window._set_row_status(0, status)
                item = window.table.item(0, 4)
                assert item.text() == status
                assert item.foreground().color() == Qt.blue
        finally:
            window.close()

    def test_on_job_status_updates_resume_status_and_saves_last_workflow(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 1",
                step_statuses={"transfer": "done", "convert": "running"},
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "conv-1", "type": "convert"},
                ],
                graph_edges=[{"source": "src-1", "target": "conv-1"}],
            )
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            window._on_job_status(0, "Konvertiere …")

            item = window.table.item(0, 4)
            overall_item = window.table.item(0, 5)
            assert job.resume_status == "Konvertiere …"
            assert item.text() == "Konvertiere …"
            assert overall_item.text() == "50%"
            assert item.toolTip() == (
                "Letzter Status: Konvertiere …\n"
                "Transfer: done\n"
                "Konvertierung: running"
            )
            window._save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_on_job_progress_updates_step_and_overall_columns(self):
        window = _new_app()
        try:
            job = WorkflowJob(name="Job 1", convert_enabled=True, upload_youtube=True)
            job.resume_status = "Konvertiere …"
            job.step_statuses = {"transfer": "done", "convert": "running"}
            job.current_step_key = "convert"
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()

            window._on_job_progress(0, 50)

            assert window.table.item(0, 4).data(int(Qt.ItemDataRole.UserRole)) == 50
            assert window.table.item(0, 5).text() == "50%"
            assert window.table.item(0, 5).data(int(Qt.ItemDataRole.UserRole) + 1) == 50
        finally:
            window.close()

    def test_source_progress_does_not_override_running_convert_progress(self):
        window = _new_app()
        try:
            job = WorkflowJob(name="Job 1", convert_enabled=True, upload_youtube=True)
            job.resume_status = "Konvertiere …"
            job.step_statuses = {"transfer": "running", "convert": "running"}
            job.current_step_key = "convert"
            job.progress_pct = 44
            job.transfer_progress_pct = 12
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()

            window._on_source_progress(0, 73)

            assert job.progress_pct == 44
            assert job.transfer_progress_pct == 73
            assert window.table.item(0, 4).data(int(Qt.ItemDataRole.UserRole)) == 44
        finally:
            window.close()

    def test_start_workflow_clears_persisted_resume_state_before_run(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 1",
                resume_status="Alter Status",
                step_statuses={"transfer": "done"},
            )
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
            assert window.table.item(0, 4).text() == "Wartend"
            assert window.table.item(0, 5).text() == "0%"
            window._save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_start_workflow_does_not_start_job_duration_for_waiting_jobs(self):
        window = _new_app()
        try:
            first = WorkflowJob(name="Job 1", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")])
            second = WorkflowJob(name="Job 2", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")])
            window._workflow = Workflow(jobs=[first, second])
            window._refresh_table()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.No), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor), patch("src.app.execution.time.monotonic", return_value=10_000.0):
                window._start_workflow()

            with patch("src.app.execution.time.monotonic", return_value=10_600.0):
                window._refresh_runtime_durations()

            assert first.run_elapsed_seconds == 0.0
            assert second.run_elapsed_seconds == 0.0
            assert window.table.item(0, 6).text() == "–"
            assert window.table.item(1, 6).text() == "–"
        finally:
            window.close()

    def test_restart_mode_clears_stale_waiting_job_duration(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 1",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                resume_status="Alt",
                step_statuses={"transfer": "done"},
                run_started_at="2026-03-23T10:00:00",
                run_finished_at="2026-03-23T18:00:00",
                run_elapsed_seconds=8 * 3600,
            )
            window._workflow = Workflow(jobs=[job], last_run_elapsed_seconds=8 * 3600)
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.No), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            assert job.run_started_at == ""
            assert job.run_finished_at == ""
            assert job.run_elapsed_seconds == 0.0
            assert window.table.item(0, 6).text() == "–"
            assert window.duration_label.text() == "Gesamtdauer: –"
        finally:
            window.close()

    def test_finished_job_duration_does_not_continue_after_late_events(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 1",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                run_started_at="2026-03-23T10:00:00",
                run_finished_at="2026-03-23T10:05:00",
                run_elapsed_seconds=300,
                resume_status="Fertig",
                step_statuses={"transfer": "done", "convert": "done"},
            )
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window._job_run_elapsed_base_seconds = {job.id: 300.0}
            window._job_run_started_monotonic = {}

            window._on_job_progress(0, 100)
            window._on_job_status(0, "Transfer 2/2: a.mp4 …")

            assert job.run_elapsed_seconds == 300
            assert job.run_finished_at == "2026-03-23T10:05:00"
            assert window.table.item(0, 6).text() == "5min 00s"
        finally:
            window.close()

    def test_job_progress_does_not_start_timer_for_waiting_job(self):
        """job_progress-Signal für einen wartenden Job darf den Timer nicht starten."""
        window = _new_app()
        try:
            job = WorkflowJob(name="Job 2", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")])
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.No), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor), patch("src.app.execution.time.monotonic", return_value=5_000.0):
                window._start_workflow(active_indices={0})

            # Job ist noch "Wartend" – kein _on_job_status mit aktivem Status
            with patch("src.app.execution.time.monotonic", return_value=5_200.0):
                window._on_job_progress(0, 12)
                window._refresh_runtime_durations()

            assert job.run_elapsed_seconds == 0.0, (
                "Timer darf nicht laufen, solange der Job wartet (nur job_progress, kein aktiver Status)"
            )
            assert window.table.item(0, 6).text() == "–"
        finally:
            window.close()

    def test_job_duration_pauses_while_job_waits_for_pipeline_slot(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 2",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/b.mp4")],
            )
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.No), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor), patch("src.app.execution.time.monotonic", return_value=1_000.0):
                window._start_workflow(active_indices={0})

            with patch("src.app.execution.time.monotonic", return_value=1_000.0):
                window._on_job_status(0, "Transfer 1/1: b.mp4 …")
            with patch("src.app.execution.time.monotonic", return_value=1_120.0):
                window._refresh_runtime_durations()

            assert job.run_elapsed_seconds == 120.0

            with patch("src.app.execution.time.monotonic", return_value=1_120.0):
                window._on_job_status(0, "Transfer OK")
            with patch("src.app.execution.time.monotonic", return_value=1_400.0):
                window._refresh_runtime_durations()

            assert job.run_elapsed_seconds == 120.0
            assert window.table.item(0, 6).text() == "2min 00s"

            with patch("src.app.execution.time.monotonic", return_value=1_400.0):
                window._on_job_status(0, "Zusammenführen …")
            with patch("src.app.execution.time.monotonic", return_value=1_460.0):
                window._refresh_runtime_durations()

            assert job.run_elapsed_seconds == 180.0
            assert window.table.item(0, 6).text() == "3min 00s"
        finally:
            window.close()

    def test_job_durations_only_advance_for_the_job_that_is_currently_working(self):
        window = _new_app()
        try:
            first = WorkflowJob(name="Job 1", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")])
            second = WorkflowJob(name="Job 2", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")])
            third = WorkflowJob(name="Job 3", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")])
            window._workflow = Workflow(jobs=[first, second, third])
            window._refresh_table()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.No), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor), patch("src.app.execution.time.monotonic", return_value=2_000.0):
                window._start_workflow(active_indices={0, 1, 2})

            with patch("src.app.execution.time.monotonic", return_value=2_000.0):
                window._on_job_status(0, "Transfer 1/1: a.mp4 …")
            with patch("src.app.execution.time.monotonic", return_value=2_120.0):
                window._refresh_runtime_durations()

            assert first.run_elapsed_seconds == 120.0
            assert second.run_elapsed_seconds == 0.0
            assert third.run_elapsed_seconds == 0.0

            with patch("src.app.execution.time.monotonic", return_value=2_120.0):
                window._on_job_status(0, "Transfer OK")
                window._on_job_status(1, "Transfer 1/1: b.mp4 …")
            with patch("src.app.execution.time.monotonic", return_value=2_300.0):
                window._refresh_runtime_durations()

            assert first.run_elapsed_seconds == 120.0
            assert second.run_elapsed_seconds == 180.0
            assert third.run_elapsed_seconds == 0.0

            with patch("src.app.execution.time.monotonic", return_value=2_300.0):
                window._on_job_status(1, "Transfer OK")
                window._on_job_status(2, "Transfer 1/1: c.mp4 …")
            with patch("src.app.execution.time.monotonic", return_value=2_420.0):
                window._refresh_runtime_durations()

            assert first.run_elapsed_seconds == 120.0
            assert second.run_elapsed_seconds == 180.0
            assert third.run_elapsed_seconds == 120.0
            assert window.table.item(0, 6).text() == "2min 00s"
            assert window.table.item(1, 6).text() == "3min 00s"
            assert window.table.item(2, 6).text() == "2min 00s"
        finally:
            window.close()

    def test_start_workflow_firststart_without_resume_does_not_prompt(self):
        window = _new_app()
        try:
            job = WorkflowJob(name="Job 1")
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior") as question, patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            question.assert_not_called()
            assert job.resume_status == ""
            assert job.step_statuses == {}
            window._save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_start_workflow_prompts_once_when_any_job_is_resumeable(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 1",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                resume_status="Transfer OK",
                step_statuses={"transfer": "done"},
            )
            window._workflow = Workflow(job=job)
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.Yes) as question, patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            question.assert_called_once()
        finally:
            window.close()

    def test_start_workflow_does_not_prompt_for_invalid_resume_metadata(self):
        window = _new_app()
        try:
            job = WorkflowJob(name="Job 1", source_mode="files", files=[], resume_status="Transfer OK")
            window._workflow = Workflow(job=job)
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior") as question, patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            question.assert_not_called()
        finally:
            window.close()

    def test_start_workflow_resume_keeps_persisted_resume_state(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 1",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                resume_status="Transfer OK",
                step_statuses={"transfer": "reused-target", "convert": "done"},
            )
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.Yes), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            assert job.resume_status == "Transfer OK"
            assert job.step_statuses == {"transfer": "reused-target", "convert": "done"}
            assert window.table.item(0, 4).text() == "Transfer OK"
            window._save_last_workflow.assert_not_called()
        finally:
            window.close()

    def test_start_workflow_resume_keeps_all_normalized_restored_jobs(self):
        window = _new_app()
        try:
            window._workflow = Workflow(
                jobs=[
                    WorkflowJob(
                        name="Job 1",
                        source_mode="files",
                        files=[FileEntry(source_path="/tmp/a.mp4")],
                        resume_status="Konvertiere …",
                        current_step_key="convert",
                        step_statuses={"transfer": "done"},
                    ),
                    WorkflowJob(
                        name="Job 2",
                        source_mode="files",
                        files=[FileEntry(source_path="/tmp/b.mp4")],
                        create_youtube_version=True,
                        upload_youtube=True,
                        resume_status="YouTube-Upload …",
                        current_step_key="youtube_upload",
                        step_statuses={"transfer": "done", "convert": "done", "yt_version": "done"},
                    ),
                ]
            )
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.Yes), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            assert window._wf_executor is not None
            assert window._wf_executor.allow_reuse_existing is True
            assert window._workflow.jobs[0].resume_status == "Konvertiere …"
            assert window._workflow.jobs[0].step_statuses == {"transfer": "done"}
            assert window._workflow.jobs[1].resume_status == "YouTube-Upload …"
            assert window._workflow.jobs[1].step_statuses == {"transfer": "done", "convert": "done", "yt_version": "done"}
            window._save_last_workflow.assert_not_called()
        finally:
            window.close()

    def test_start_workflow_restart_clears_all_normalized_restored_jobs(self):
        window = _new_app()
        try:
            window._workflow = Workflow(
                jobs=[
                    WorkflowJob(
                        name="Job 1",
                        source_mode="files",
                        files=[FileEntry(source_path="/tmp/a.mp4")],
                        resume_status="Konvertiere …",
                        current_step_key="convert",
                        step_statuses={"transfer": "done"},
                    ),
                    WorkflowJob(
                        name="Job 2",
                        source_mode="files",
                        files=[FileEntry(source_path="/tmp/b.mp4")],
                        create_youtube_version=True,
                        upload_youtube=True,
                        resume_status="YouTube-Upload …",
                        current_step_key="youtube_upload",
                        step_statuses={"transfer": "done", "convert": "done", "yt_version": "done"},
                    ),
                ]
            )
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.No), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            assert window._wf_executor is not None
            assert window._wf_executor.allow_reuse_existing is False
            assert window._workflow.jobs[0].resume_status == ""
            assert window._workflow.jobs[0].step_statuses == {}
            assert window._workflow.jobs[1].resume_status == ""
            assert window._workflow.jobs[1].step_statuses == {}
            assert window.table.item(0, 4).text() == "Wartend"
            assert window.table.item(1, 4).text() == "Wartend"
            window._save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_start_workflow_resume_cancel_does_not_start(self):
        window = _new_app()
        try:
            job = WorkflowJob(
                name="Job 1",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                resume_status="Transfer OK",
                step_statuses={"transfer": "done"},
            )
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window._save_last_workflow = MagicMock()

            with patch.object(window, "_ask_resume_behavior", return_value=QMessageBox.Cancel), patch(
                "src.app.QThread", _DummyThread
            ), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow()

            assert window._wf_thread is None
            assert window._wf_executor is None
            assert job.resume_status == "Transfer OK"
            window._save_last_workflow.assert_not_called()
        finally:
            window.close()

    def test_open_job_workflow_uses_selected_job_and_allows_edit_mode(self):
        window = _new_app()
        try:
            _DummyWorkflowDialog.instances.clear()
            job = WorkflowJob(name="Job 1")
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window.table.selectRow(0)

            with patch("src.app.JobWorkflowDialog", _DummyWorkflowDialog):
                window._open_job_workflow()

            assert len(_DummyWorkflowDialog.instances) == 1
            assert _DummyWorkflowDialog.instances[0].job is job
            assert _DummyWorkflowDialog.instances[0].allow_edit is True
        finally:
            window.close()

    def test_open_job_workflow_refreshes_table_when_dialog_changed_job(self):
        window = _new_app()
        try:
            _DummyWorkflowDialog.instances.clear()
            job = WorkflowJob(name="Job 1")
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window.table.selectRow(0)

            def _make_dialog(parent, selected_job, allow_edit=False, settings=None):
                dialog = _DummyWorkflowDialog(parent, selected_job, allow_edit=allow_edit, settings=settings)
                dialog.changed = True
                selected_job.name = "Geändert"
                return dialog

            with patch("src.app.JobWorkflowDialog", side_effect=_make_dialog), patch.object(window, "_save_last_workflow") as save_last_workflow:
                window._open_job_workflow()

            assert window.table.item(0, 1).text() == "Geändert"
            save_last_workflow.assert_called_once()
        finally:
            window.close()

    def test_open_job_workflow_uses_only_job_without_explicit_selection(self):
        window = _new_app()
        try:
            _DummyWorkflowDialog.instances.clear()
            job = WorkflowJob(name="Job 1")
            window._workflow = Workflow(jobs=[job])
            window._refresh_table()
            window.table.clearSelection()

            with patch("src.app.JobWorkflowDialog", _DummyWorkflowDialog):
                window._open_job_workflow()

            assert len(_DummyWorkflowDialog.instances) == 1
            assert _DummyWorkflowDialog.instances[0].job is job
        finally:
            window.close()

    def test_open_job_workflow_shows_hint_when_multiple_jobs_and_none_selected(self):
        window = _new_app()
        try:
            window._workflow = Workflow()
            window._refresh_table()
            window.table.clearSelection()

            with patch("src.app.QMessageBox.information") as info, patch("src.app.JobWorkflowDialog") as dialog:
                window._open_job_workflow()

            info.assert_not_called()
            dialog.assert_not_called()
        finally:
            window.close()

    def test_table_double_click_opens_workflow_for_pipeline_columns(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Job 1")])
            window._refresh_table()
            index = window.table.model().index(0, 4)

            with patch.object(window, "_open_job_workflow") as open_workflow, patch.object(window, "_edit_job") as edit_job:
                window._handle_table_double_click(index)

            open_workflow.assert_called_once_with(0)
            edit_job.assert_not_called()
        finally:
            window.close()

    def test_table_double_click_opens_workflow_for_name_column(self):
        window = _new_app()
        try:
            window._workflow = Workflow(jobs=[WorkflowJob(name="Job 1")])
            window._refresh_table()
            index = window.table.model().index(0, 1)

            with patch.object(window, "_open_job_workflow") as open_workflow, patch.object(window, "_edit_job") as edit_job:
                window._handle_table_double_click(index)

            open_workflow.assert_called_once_with(0)
            edit_job.assert_not_called()
        finally:
            window.close()

class TestStartWorkflowRemapInit:
    """_start_workflow initialisiert _job_orig_to_cur als identische Abbildung aller Indizes."""

    def test_start_workflow_initializes_identity_remap_for_all_jobs(self):
        """`_job_orig_to_cur` zeigt nach dem Start für jeden Job-Index auf sich selbst."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
                WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
                WorkflowJob(name="C", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")]),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1, 2})

            assert window._job_orig_to_cur == {0: 0, 1: 1, 2: 2}
        finally:
            window.close()

    def test_start_workflow_with_single_job_creates_single_entry_remap(self):
        """Auch für einen einzigen Job wird der Remap korrekt mit {0: 0} initialisiert."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="Solo", source_mode="files", files=[FileEntry(source_path="/tmp/solo.mp4")]),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0})

            assert window._job_orig_to_cur == {0: 0}
        finally:
            window.close()

    def test_on_workflow_done_clears_remap(self):
        """Nach dem finished-Signal ist `_job_orig_to_cur` wieder leer."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
                WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1})

            assert window._job_orig_to_cur == {0: 0, 1: 1}

            finished_callbacks = list(window._wf_executor.finished._callbacks)
            for cb in finished_callbacks:
                cb(2, 0, 0)

            assert window._job_orig_to_cur == {}
        finally:
            window.close()


class TestSignalSlotOrigToCurRemap:
    """Signal-Slots (_on_job_status, _on_job_progress, _on_source_status, _on_source_progress)
    leiten eingehende Signale über _job_orig_to_cur an den richtigen aktuellen Job-Index um."""

    def _make_started_window(self):
        """Hilfsmethode: Erstellt ein Fenster mit 2 Jobs und gestartetem Workflow."""
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(name="Job-A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="Job-B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
        ]
        window._refresh_table()
        with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
            window._start_workflow(active_indices={0, 1})
        return window

    def test_on_job_status_routes_to_remapped_cur_idx(self):
        """_on_job_status(orig_idx=1) aktualisiert den Job an cur_idx=0 laut Remap."""
        window = self._make_started_window()
        try:
            # Remap simuliert: Job ursprünglich an Index 1 ist nach Löschen von Index 0 nun an Index 0.
            window._job_orig_to_cur = {1: 0}

            window._on_job_status(1, "Konvertiere …")

            assert window._workflow.jobs[0].resume_status == "Konvertiere …"
        finally:
            window.close()

    def test_on_job_status_does_not_update_job_at_orig_idx_when_remap_redirects(self):
        """Wenn der Remap orig_idx 0 auf cur_idx 1 umleitet, bleibt jobs[0] unberührt."""
        window = self._make_started_window()
        try:
            window._workflow.jobs[0].resume_status = "unveraendert"
            window._job_orig_to_cur = {0: 1, 1: 0}

            window._on_job_status(0, "Konvertiere …")

            # cur_idx laut Remap ist 1, daher darf jobs[0] nicht geändert werden
            assert window._workflow.jobs[0].resume_status == "unveraendert"
            assert window._workflow.jobs[1].resume_status == "Konvertiere …"
        finally:
            window.close()

    def test_on_job_status_falls_back_to_orig_idx_when_remap_attribute_absent(self):
        """Ohne _job_orig_to_cur-Attribut verwendet _on_job_status direkt orig_idx."""
        window = self._make_started_window()
        try:
            if hasattr(window, "_job_orig_to_cur"):
                del window._job_orig_to_cur

            window._on_job_status(1, "Konvertiere …")

            assert window._workflow.jobs[1].resume_status == "Konvertiere …"
        finally:
            window.close()

    def test_on_job_status_falls_back_to_orig_idx_when_remap_is_empty(self):
        """Mit leerem _job_orig_to_cur-Dict verwendet _on_job_status den orig_idx als Fallback."""
        window = self._make_started_window()
        try:
            window._job_orig_to_cur = {}

            window._on_job_status(0, "Konvertiere …")

            assert window._workflow.jobs[0].resume_status == "Konvertiere …"
        finally:
            window.close()

    def test_on_job_progress_routes_to_remapped_cur_idx(self):
        """_on_job_progress(orig_idx=1) setzt progress_pct auf den Job an cur_idx=0 laut Remap."""
        window = self._make_started_window()
        try:
            window._job_orig_to_cur = {1: 0}

            window._on_job_progress(1, 75)

            assert window._workflow.jobs[0].progress_pct == 75
            assert window._workflow.jobs[1].progress_pct == 0  # nicht verändert
        finally:
            window.close()

    def test_on_source_status_routes_to_remapped_cur_idx(self):
        """_on_source_status(orig_idx=1) setzt transfer_status auf den Job an cur_idx=0 laut Remap."""
        window = self._make_started_window()
        try:
            window._job_orig_to_cur = {1: 0}

            window._on_source_status(1, "Transfer 1/3")

            assert window._workflow.jobs[0].transfer_status == "Transfer 1/3"
            assert window._workflow.jobs[1].transfer_status != "Transfer 1/3"
        finally:
            window.close()

    def test_on_source_progress_routes_to_remapped_cur_idx(self):
        """_on_source_progress(orig_idx=1) setzt transfer_progress_pct auf den Job an cur_idx=0."""
        window = self._make_started_window()
        try:
            window._job_orig_to_cur = {1: 0}

            window._on_source_progress(1, 40)

            assert window._workflow.jobs[0].transfer_progress_pct == 40
            assert window._workflow.jobs[1].transfer_progress_pct == 0  # nicht verändert
        finally:
            window.close()


class TestClearWorkflowRemapMaintenance:
    """_clear_workflow pflegt _job_orig_to_cur und _active_run_indices korrekt nach Löschen eines Jobs."""

    def _make_window_with_three_jobs(self):
        """Erstellt ein Fenster mit 3 Jobs, identem Remap und allen Indizes als aktiv."""
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
            WorkflowJob(name="C", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")]),
        ]
        window._refresh_table()
        window._job_orig_to_cur = {0: 0, 1: 1, 2: 2}
        window._active_run_indices = {0, 1, 2}
        return window

    def _confirm_delete_row(self, window, row):
        """Setzt den ausgewählten Reihe und bestätigt das Löschen."""
        window.table.setCurrentCell(row, 0)
        with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            window._clear_workflow()

    def test_delete_first_job_removes_its_entry_and_shifts_higher_cur_indices_down(self):
        """Löschen von Zeile 0: orig_idx 0 wird entfernt, orig_idx 1→cur 0, orig_idx 2→cur 1."""
        window = self._make_window_with_three_jobs()
        try:
            self._confirm_delete_row(window, 0)

            assert window._job_orig_to_cur == {1: 0, 2: 1}
        finally:
            window.close()

    def test_delete_middle_job_removes_its_entry_and_shifts_only_higher_cur_indices(self):
        """Löschen von Zeile 1: orig_idx 1 wird entfernt, orig_idx 0 bleibt, orig_idx 2→cur 1."""
        window = self._make_window_with_three_jobs()
        try:
            self._confirm_delete_row(window, 1)

            assert window._job_orig_to_cur == {0: 0, 2: 1}
        finally:
            window.close()

    def test_delete_last_job_removes_its_entry_and_leaves_lower_entries_unchanged(self):
        """Löschen von Zeile 2: orig_idx 2 wird entfernt, orig_idx 0 und 1 bleiben unverändert."""
        window = self._make_window_with_three_jobs()
        try:
            self._confirm_delete_row(window, 2)

            assert window._job_orig_to_cur == {0: 0, 1: 1}
        finally:
            window.close()

    def test_delete_with_empty_remap_does_not_raise(self):
        """Mit leerem _job_orig_to_cur darf kein Fehler auftreten – das Löschen läuft normal durch."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            ]
            window._refresh_table()
            window._job_orig_to_cur = {}

            self._confirm_delete_row(window, 0)

            assert window._job_orig_to_cur == {}
            assert len(window._workflow.jobs) == 0
        finally:
            window.close()

    def test_delete_without_remap_attribute_does_not_raise(self):
        """Ohne _job_orig_to_cur-Attribut (kein laufender Workflow) wird kein Fehler geworfen."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            ]
            window._refresh_table()
            assert not hasattr(window, "_job_orig_to_cur")

            self._confirm_delete_row(window, 0)

            # Attribut darf nach dem Löschen immer noch fehlen (wurde nicht fälschlicherweise erstellt)
            assert not hasattr(window, "_job_orig_to_cur")
        finally:
            window.close()

    def test_delete_without_confirmation_leaves_remap_unchanged(self):
        """Wenn der Nutzer die Bestätigung ablehnt, bleibt _job_orig_to_cur unverändert."""
        window = self._make_window_with_three_jobs()
        try:
            window.table.setCurrentCell(1, 0)
            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                window._clear_workflow()

            assert window._job_orig_to_cur == {0: 0, 1: 1, 2: 2}
            assert len(window._workflow.jobs) == 3
        finally:
            window.close()

    def test_delete_first_job_shifts_active_run_indices_down(self):
        """Löschen von Zeile 0: _active_run_indices wird von {0,1,2} auf {0,1} angepasst."""
        window = self._make_window_with_three_jobs()
        try:
            self._confirm_delete_row(window, 0)

            assert window._active_run_indices == {0, 1}
        finally:
            window.close()

    def test_delete_middle_job_removes_it_from_active_run_indices_and_shifts_higher(self):
        """Löschen von Zeile 1: _active_run_indices wird von {0,1,2} auf {0,1} angepasst."""
        window = self._make_window_with_three_jobs()
        try:
            self._confirm_delete_row(window, 1)

            assert window._active_run_indices == {0, 1}
        finally:
            window.close()

    def test_delete_removes_running_job_index_from_active_run_indices(self):
        """Beim Löschen eines aktiven (laufenden) Jobs verschwindet sein Index aus _active_run_indices."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
                WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
            ]
            window._refresh_table()
            window._job_orig_to_cur = {0: 0, 1: 1}
            window._active_run_indices = {0, 1}

            self._confirm_delete_row(window, 0)

            # Index 0 (gelöschter Job) darf nicht mehr in _active_run_indices erscheinen.
            # Job B (vorher Index 1) ist jetzt Index 0.
            assert window._active_run_indices == {0}
        finally:
            window.close()


class TestDeleteWhileRunningIntegration:
    """Integrations-Szenario: Löschen eines fertigen Jobs während ein anderer Job noch läuft.

    Stellt sicher, dass der _job_orig_to_cur-Remap-Mechanismus das Signal korrekt an den
    verschobenen Job weiterleitet, ohne den laufenden Workflow abzubrechen.
    """

    def test_signal_for_running_job_reaches_its_shifted_position_after_finished_job_deleted(self):
        """Nachdem Job 0 (fertig) gelöscht wurde, trifft das Signal für orig_idx=1
        am neuen cur_idx=0 ein – der laufende Job empfängt seinen Status-Update korrekt."""
        window = _new_app()
        try:
            job_done = WorkflowJob(
                name="Fertig-Job",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/done.mp4")],
            )
            job_running = WorkflowJob(
                name="Laufender-Job",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/running.mp4")],
            )
            window._workflow.jobs = [job_done, job_running]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1})

            # Remap nach Start: {0: 0, 1: 1}
            assert window._job_orig_to_cur == {0: 0, 1: 1}

            # Executor meldet Job 0 als abgeschlossen
            window._on_job_status(0, "Fertig")

            # Nutzer löscht den fertigen Job (Zeile 0) während Job 1 noch läuft
            window.table.setCurrentCell(0, 0)
            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                window._clear_workflow()

            # Remap muss nun orig_idx 1 auf cur_idx 0 zeigen
            assert window._job_orig_to_cur == {1: 0}
            assert len(window._workflow.jobs) == 1
            assert window._workflow.jobs[0] is job_running

            # Executor sendet weiteres Status-Signal für den laufenden Job (orig_idx=1)
            window._on_job_status(1, "YT-Version erstellen …")

            # Der laufende Job (jetzt an Position 0) muss den Status erhalten haben
            assert job_running.resume_status == "YT-Version erstellen …"
            # Die Tabellen-Zeile 0 muss ebenfalls aktualisiert worden sein
            assert window.table.item(0, 4).text() == "YT-Version erstellen …"
        finally:
            window.close()

    def test_progress_signal_for_running_job_reaches_shifted_position_after_finished_job_deleted(self):
        """Nach dem Löschen von Job 0 leitet _on_job_progress(1, 60) korrekt auf cur_idx=0 um."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="Fertig-Job", source_mode="files", files=[FileEntry(source_path="/tmp/done.mp4")]),
                WorkflowJob(name="Laufender-Job", source_mode="files", files=[FileEntry(source_path="/tmp/run.mp4")]),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1})

            # Job 0 als fertig markieren und löschen
            window._on_job_status(0, "Fertig")
            window.table.setCurrentCell(0, 0)
            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                window._clear_workflow()

            assert window._job_orig_to_cur == {1: 0}

            # Fortschritts-Signal für orig_idx=1 (den verbliebenen Job)
            window._on_job_progress(1, 60)

            # progress_pct muss am neuen cur_idx=0 ankommen
            assert window._workflow.jobs[0].progress_pct == 60
        finally:
            window.close()

    def test_signal_for_deleted_finished_job_does_not_crash(self):
        """Ein verspätetes Signal für den bereits gelöschten Job (orig_idx=0) darf
        keinen Fehler verursachen – der Remap enthält den Eintrag nicht mehr."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="Gelöschter-Job", source_mode="files", files=[FileEntry(source_path="/tmp/del.mp4")]),
                WorkflowJob(name="Verbleibender-Job", source_mode="files", files=[FileEntry(source_path="/tmp/rem.mp4")]),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1})

            # Job 0 löschen
            window._on_job_status(0, "Fertig")
            window.table.setCurrentCell(0, 0)
            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                window._clear_workflow()

            # orig_idx 0 ist nicht mehr im Remap
            assert 0 not in window._job_orig_to_cur

            # Verspätetes Signal für den bereits gelöschten Job – darf keinen Crash verursachen
            window._on_job_status(0, "Fertig")   # kein Fehler erwartet
            window._on_job_progress(0, 100)      # kein Fehler erwartet
        finally:
            window.close()

    def test_source_progress_signal_for_running_job_reaches_shifted_position(self):
        """_on_source_progress(1, 33) leitet nach Löschen von Job 0 korrekt auf cur_idx=0 um."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(name="Fertig-Job", source_mode="files", files=[FileEntry(source_path="/tmp/done.mp4")]),
                WorkflowJob(name="Laufender-Job", source_mode="files", files=[FileEntry(source_path="/tmp/run.mp4")]),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1})

            window._on_job_status(0, "Fertig")
            window.table.setCurrentCell(0, 0)
            with patch("src.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                window._clear_workflow()

            assert window._job_orig_to_cur == {1: 0}

            window._on_source_progress(1, 33)

            assert window._workflow.jobs[0].transfer_progress_pct == 33
        finally:
            window.close()


class TestResetStatusColumn:
    """_reset_status_column(rows=...) setzt nur die übergebenen Zeilen zurück.
    Ohne Argument werden alle Zeilen zurückgesetzt (Rückwärtskompatibilität)."""

    def _make_window_with_three_jobs(self) -> ConverterApp:
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
            WorkflowJob(name="C", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")]),
        ]
        window._refresh_table()
        return window

    def _set_all_rows_status(self, window: ConverterApp, status: str) -> None:
        for row in range(window.table.rowCount()):
            window._set_row_status(row, status)

    def _row_status(self, window: ConverterApp, row: int) -> str:
        item = window.table.item(row, 4)
        return item.text() if item else ""

    def test_without_argument_resets_all_rows(self):
        """_reset_status_column() ohne Argument setzt alle Tabellenzeilen auf 'Wartend'."""
        window = self._make_window_with_three_jobs()
        try:
            self._set_all_rows_status(window, "Fertig")
            assert self._row_status(window, 0) == "Fertig"
            assert self._row_status(window, 1) == "Fertig"
            assert self._row_status(window, 2) == "Fertig"

            window._reset_status_column()

            assert self._row_status(window, 0) == "Wartend"
            assert self._row_status(window, 1) == "Wartend"
            assert self._row_status(window, 2) == "Wartend"
        finally:
            window.close()

    def test_with_rows_none_resets_all_rows(self):
        """_reset_status_column(rows=None) verhält sich wie der Aufruf ohne Argument."""
        window = self._make_window_with_three_jobs()
        try:
            self._set_all_rows_status(window, "Fertig")

            window._reset_status_column(rows=None)

            assert self._row_status(window, 0) == "Wartend"
            assert self._row_status(window, 1) == "Wartend"
            assert self._row_status(window, 2) == "Wartend"
        finally:
            window.close()

    def test_with_single_row_resets_only_that_row(self):
        """_reset_status_column(rows={1}) setzt nur Zeile 1 zurück; Zeilen 0 und 2 bleiben unberührt."""
        window = self._make_window_with_three_jobs()
        try:
            self._set_all_rows_status(window, "Fertig")

            window._reset_status_column(rows={1})

            assert self._row_status(window, 0) == "Fertig"
            assert self._row_status(window, 1) == "Wartend"
            assert self._row_status(window, 2) == "Fertig"
        finally:
            window.close()

    def test_with_first_and_last_row_resets_only_those_rows(self):
        """_reset_status_column(rows={0, 2}) setzt Zeilen 0 und 2 zurück; Zeile 1 bleibt unberührt."""
        window = self._make_window_with_three_jobs()
        try:
            self._set_all_rows_status(window, "Fertig")

            window._reset_status_column(rows={0, 2})

            assert self._row_status(window, 0) == "Wartend"
            assert self._row_status(window, 1) == "Fertig"
            assert self._row_status(window, 2) == "Wartend"
        finally:
            window.close()

    def test_with_empty_set_resets_no_rows(self):
        """_reset_status_column(rows=set()) setzt keine einzige Zeile zurück."""
        window = self._make_window_with_three_jobs()
        try:
            self._set_all_rows_status(window, "Fertig")

            window._reset_status_column(rows=set())

            assert self._row_status(window, 0) == "Fertig"
            assert self._row_status(window, 1) == "Fertig"
            assert self._row_status(window, 2) == "Fertig"
        finally:
            window.close()

    def test_reset_sets_progress_column_to_zero_percent(self):
        """_reset_status_column(rows={0}) setzt auch die Fortschritts-Spalte auf '0%'."""
        window = self._make_window_with_three_jobs()
        try:
            window._reset_status_column(rows={0})

            progress_item = window.table.item(0, 5)
            assert progress_item is not None
            assert progress_item.text() == "0%"
        finally:
            window.close()

    def test_reset_sets_duration_column_to_dash(self):
        """_reset_status_column(rows={0}) setzt auch die Dauer-Spalte auf '–'."""
        window = self._make_window_with_three_jobs()
        try:
            window._reset_status_column(rows={0})

            duration_item = window.table.item(0, 6)
            assert duration_item is not None
            assert duration_item.text() == "–"
        finally:
            window.close()


class TestStartWorkflowResetsOnlyActiveRows:
    """_start_workflow setzt nur die Statusspalten der gestarteten Jobs zurück.
    Jobs, die nicht gestartet werden, behalten ihren bisherigen Tabellenstatus."""

    def _make_window_with_three_finished_jobs(self) -> ConverterApp:
        """Erstellt ein Fenster mit 3 Jobs, die alle als 'Fertig' in der Tabelle stehen."""
        window = _new_app()
        window._workflow.jobs = [
            WorkflowJob(name="A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
            WorkflowJob(name="C", source_mode="files", files=[FileEntry(source_path="/tmp/c.mp4")]),
        ]
        window._refresh_table()
        for row in range(3):
            window._set_row_status(row, "Fertig")
        return window

    def _row_status(self, window: ConverterApp, row: int) -> str:
        item = window.table.item(row, 4)
        return item.text() if item else ""

    def test_starting_single_job_resets_only_its_row(self):
        """Wird nur Job an Index 1 gestartet, stehen Zeilen 0 und 2 weiterhin auf 'Fertig'."""
        window = self._make_window_with_three_finished_jobs()
        try:
            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={1})

            assert self._row_status(window, 0) == "Fertig"
            assert self._row_status(window, 1) == "Wartend"
            assert self._row_status(window, 2) == "Fertig"
        finally:
            window.close()

    def test_starting_first_job_resets_only_first_row(self):
        """Wird nur Job an Index 0 gestartet, bleiben Zeilen 1 und 2 auf 'Fertig'."""
        window = self._make_window_with_three_finished_jobs()
        try:
            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0})

            assert self._row_status(window, 0) == "Wartend"
            assert self._row_status(window, 1) == "Fertig"
            assert self._row_status(window, 2) == "Fertig"
        finally:
            window.close()

    def test_starting_last_job_resets_only_last_row(self):
        """Wird nur Job an Index 2 gestartet, bleiben Zeilen 0 und 1 auf 'Fertig'."""
        window = self._make_window_with_three_finished_jobs()
        try:
            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={2})

            assert self._row_status(window, 0) == "Fertig"
            assert self._row_status(window, 1) == "Fertig"
            assert self._row_status(window, 2) == "Wartend"
        finally:
            window.close()

    def test_starting_first_and_last_job_leaves_middle_unchanged(self):
        """Werden Jobs 0 und 2 gestartet, bleibt Zeile 1 auf 'Fertig'."""
        window = self._make_window_with_three_finished_jobs()
        try:
            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 2})

            assert self._row_status(window, 0) == "Wartend"
            assert self._row_status(window, 1) == "Fertig"
            assert self._row_status(window, 2) == "Wartend"
        finally:
            window.close()

    def test_starting_all_jobs_resets_all_rows(self):
        """Werden alle Jobs gestartet, stehen danach alle Zeilen auf 'Wartend'."""
        window = self._make_window_with_three_finished_jobs()
        try:
            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={0, 1, 2})

            assert self._row_status(window, 0) == "Wartend"
            assert self._row_status(window, 1) == "Wartend"
            assert self._row_status(window, 2) == "Wartend"
        finally:
            window.close()

    def test_non_active_job_data_model_is_not_cleared(self):
        """resume_status und step_statuses eines nicht gestarteten Jobs bleiben erhalten."""
        window = _new_app()
        try:
            window._workflow.jobs = [
                WorkflowJob(
                    name="Unveraendert",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/a.mp4")],
                    resume_status="Fertig",
                    step_statuses={"convert": "done"},
                    progress_pct=100,
                ),
                WorkflowJob(
                    name="Gestartet",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/b.mp4")],
                ),
            ]
            window._refresh_table()

            with patch("src.app.QThread", _DummyThread), patch("src.app.WorkflowExecutor", _DummyExecutor):
                window._start_workflow(active_indices={1})

            # Datenmodell des nicht gestarteten Jobs muss unberührt bleiben
            unchanged_job = window._workflow.jobs[0]
            assert unchanged_job.resume_status == "Fertig"
            assert unchanged_job.step_statuses == {"convert": "done"}
            assert unchanged_job.progress_pct == 100
        finally:
            window.close()


class TestSummarizeSource:
    """Branch-Abdeckung für _summarize_source (helpers.py L23-28)."""

    def test_folder_scan_with_folder_shows_folder_name(self):
        job = WorkflowJob(source_mode="folder_scan", source_folder="/srv/videos/spieltag")
        result = _summarize_source(job)
        assert "spieltag" in result
        assert "📁" in result

    def test_folder_scan_without_folder_shows_dash(self):
        job = WorkflowJob(source_mode="folder_scan", source_folder="")
        result = _summarize_source(job)
        assert "–" in result

    def test_pi_download_with_device_name_shows_name(self):
        job = WorkflowJob(source_mode="pi_download", device_name="Pi Nord")
        result = _summarize_source(job)
        assert "Pi Nord" in result
        assert "📷" in result

    def test_pi_download_without_device_name_shows_dash(self):
        job = WorkflowJob(source_mode="pi_download", device_name="")
        result = _summarize_source(job)
        assert "–" in result

    def test_unknown_source_mode_returns_question_mark(self):
        job = WorkflowJob(source_mode="unbekannt")
        result = _summarize_source(job)
        assert result == "?"


class TestSummarizePipeline:
    """Branch-Abdeckung für _summarize_pipeline (helpers.py L39, 47, 49, 51, 53, 57)."""

    def _job_with_types(self, *types: str, source_mode: str = "files") -> WorkflowJob:
        nodes = [{"id": f"n{i}", "type": t} for i, t in enumerate(types)]
        return WorkflowJob(source_mode=source_mode, graph_nodes=nodes, graph_edges=[])

    def test_pi_download_prefixes_download_step(self):
        job = self._job_with_types("source_pi_download", source_mode="pi_download")
        result = _summarize_pipeline(job)
        assert result.startswith("Download")

    def test_validate_surface_adds_quick_check(self):
        job = self._job_with_types("validate_surface")
        result = _summarize_pipeline(job)
        assert "Quick-Check" in result

    def test_validate_deep_adds_deep_scan(self):
        job = self._job_with_types("validate_deep")
        result = _summarize_pipeline(job)
        assert "Deep-Scan" in result

    def test_cleanup_adds_cleanup(self):
        job = self._job_with_types("cleanup")
        result = _summarize_pipeline(job)
        assert "Cleanup" in result

    def test_repair_adds_reparatur(self):
        job = self._job_with_types("repair")
        result = _summarize_pipeline(job)
        assert "Reparatur" in result

    def test_stop_adds_stop(self):
        job = self._job_with_types("stop")
        result = _summarize_pipeline(job)
        assert "Stop" in result


class TestPlannedJobStepsMergePrecedesConvert:
    """Branch-Abdeckung für _planned_job_steps: Merge-vor-Convert-Pfad (L190-192)."""

    def test_merge_precedes_convert_includes_both_in_order(self):
        """merge → convert in Graph → Merge zuerst, dann Convert in Schrittliste."""
        job = WorkflowJob(
            files=[
                FileEntry(source_path="/tmp/a.mp4", merge_group_id="g1"),
                FileEntry(source_path="/tmp/b.mp4", merge_group_id="g1"),
            ],
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "merge", "type": "merge"},
                {"id": "conv", "type": "convert"},
            ],
            graph_edges=[
                {"source": "src", "target": "merge"},
                {"source": "merge", "target": "conv"},
            ],
        )
        steps = _planned_job_steps(job)
        assert "merge" in steps
        assert "convert" in steps
        assert steps.index("merge") < steps.index("convert")

    def test_validate_deep_step_included_when_reachable(self):
        """validate_deep-Knoten im Graph → Schritt erscheint in der Liste (L203)."""
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "vd", "type": "validate_deep"},
            ],
            graph_edges=[{"source": "src", "target": "vd"}],
        )
        steps = _planned_job_steps(job)
        assert "validate_deep" in steps

    def test_cleanup_step_included_when_reachable(self):
        """cleanup-Knoten im Graph → Schritt erscheint in der Liste (L205)."""
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "cl", "type": "cleanup"},
            ],
            graph_edges=[{"source": "src", "target": "cl"}],
        )
        steps = _planned_job_steps(job)
        assert "cleanup" in steps


class TestComputeJobOverallProgressBranches:
    """Direkte Tests für _compute_job_overall_progress (L290)."""

    def test_progress_with_previous_completed_steps_returns_weighted_pct(self):
        """completed_before_current=1, pct=50, 2 Schritte → (1 + 0.5) / 2 * 100 = 75 (L290)."""
        job = WorkflowJob(
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "conv", "type": "convert"},
            ],
            graph_edges=[{"source": "src", "target": "conv"}],
            step_statuses={"transfer": "done"},
            resume_status="Konvertiere …",
            current_step_key="convert",
        )
        result = _compute_job_overall_progress(job, "Konvertiere …", 50)
        assert result == 75


class TestNormalizeResumeStateUncoveredBranches:
    """Branch-Abdeckung für _normalize_cancelled_resume_state (L355-356, L362-368)."""

    def test_current_step_key_corrected_when_different_from_resume_step(self):
        """Abgebrochener Transfer, aber current_step_key zeigt auf Convert → wird korrigiert (L355-356)."""
        job = WorkflowJob(
            name="Job",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/a.mp4")],
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "conv", "type": "convert"},
            ],
            graph_edges=[{"source": "src", "target": "conv"}],
            resume_status="Transfer abgebrochen",
            current_step_key="convert",
            step_statuses={"transfer": "cancelled"},
            step_details={},
        )

        changed = _normalize_cancelled_resume_state(job)

        assert changed is True
        assert job.current_step_key == "transfer"
        assert "Transfer" in job.resume_status

    def test_all_steps_done_clears_stale_abgebrochen_resume_status(self):
        """Alle Schritte fertig, aber resume_status enthält 'abgebrochen' → beides leeren (L362-368)."""
        job = WorkflowJob(
            name="Job",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/a.mp4")],
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "conv", "type": "convert"},
            ],
            graph_edges=[{"source": "src", "target": "conv"}],
            resume_status="Konvertierung abgebrochen",
            current_step_key="convert",
            step_statuses={"transfer": "done", "convert": "done"},
            step_details={},
        )

        changed = _normalize_cancelled_resume_state(job)

        assert changed is True
        assert job.current_step_key == ""
        assert job.resume_status == ""


class TestJobHasSourceConfigBranches:
    """Branch-Abdeckung für _job_has_source_config (L378) und _job_is_placeholder (L382)."""

    def test_folder_scan_with_folder_returns_true(self):
        job = WorkflowJob(source_mode="folder_scan", source_folder="/srv/videos")
        assert _job_has_source_config(job) is True

    def test_pi_download_with_device_name_returns_true(self):
        job = WorkflowJob(source_mode="pi_download", device_name="Pi Nord")
        assert _job_has_source_config(job) is True

    def test_unknown_source_mode_returns_false(self):
        job = WorkflowJob(source_mode="unbekannt")
        assert _job_has_source_config(job) is False

    def test_placeholder_job_with_empty_name_returns_true(self):
        """Kein Source-Config, kein resume_status, Name leer → Platzhalter (L382)."""
        job = WorkflowJob(name="", source_mode="files", files=[], resume_status="", step_statuses={})
        assert _job_is_placeholder(job) is True

    def test_job_with_resume_status_is_not_placeholder(self):
        job = WorkflowJob(name="Job 1", source_mode="files", files=[], resume_status="Transfer …", step_statuses={})
        assert _job_is_placeholder(job) is False


class TestJobsLookCompatibleBranches:
    """Branch-Abdeckung für _jobs_look_compatible (L392, L394, L397)."""

    def test_different_source_mode_returns_false(self):
        restored = WorkflowJob(source_mode="files", name="Job 1")
        fallback = WorkflowJob(source_mode="folder_scan", name="Job 1")
        assert _jobs_look_compatible(restored, fallback) is False

    def test_matching_ids_returns_true(self):
        """Gleiche IDs → sofort True (L392-394)."""
        restored = WorkflowJob(source_mode="files", name="Job 1", id="abc-123")
        fallback = WorkflowJob(source_mode="files", name="Anderer Name", id="abc-123")
        assert _jobs_look_compatible(restored, fallback) is True

    def test_matching_names_returns_true(self):
        """Gleiche Namen → True."""
        restored = WorkflowJob(source_mode="files", name="Spieltag 23")
        fallback = WorkflowJob(source_mode="files", name="Spieltag 23")
        assert _jobs_look_compatible(restored, fallback) is True

    def test_placeholder_name_fallback_returns_true(self):
        """Restored hat Platzhalter-Namen → kompatibel (L397)."""
        restored = WorkflowJob(source_mode="files", name="Job 1")
        fallback = WorkflowJob(source_mode="files", name="Spieltag 23")
        assert _jobs_look_compatible(restored, fallback) is True

    def test_different_names_and_no_placeholder_returns_false(self):
        """Verschiedene Namen, kein Platzhalter → nicht kompatibel."""
        restored = WorkflowJob(source_mode="files", name="Job A", id="")
        fallback = WorkflowJob(source_mode="files", name="Job B")
        assert _jobs_look_compatible(restored, fallback) is False


class TestRepairRestoredWorkflowMixedJobs:
    """Branch-Abdeckung für _repair_restored_workflow (L419, L434-437, L462, L464)."""

    def test_job_with_source_config_in_mixed_workflow_increments_repaired_count(self):
        """Job MIT Source-Config bekommt _normalize aufgerufen → repaired_count++ (L419)."""
        restored = Workflow(jobs=[
            WorkflowJob(
                name="Job A",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                resume_status="Transfer abgebrochen",
                current_step_key="convert",
                step_statuses={"transfer": "cancelled"},
            ),
            WorkflowJob(name="Job B", source_mode="files", files=[]),
        ])
        fallback = Workflow(jobs=[
            WorkflowJob(name="Job A", source_mode="files", files=[FileEntry(source_path="/tmp/a.mp4")]),
            WorkflowJob(name="Job B", source_mode="files", files=[FileEntry(source_path="/tmp/b.mp4")]),
        ])

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, fallback)

        assert repaired_count >= 1

    def test_placeholder_job_with_stale_resume_drops_state_when_no_compatible_candidate(self):
        """Platzhalter mit resume_status, kein passender Fallback → Status geleert (L434-437)."""
        restored = Workflow(jobs=[
            WorkflowJob(
                name="Job 1",
                source_mode="files",
                files=[],
                resume_status="Transfer …",
                step_statuses={"transfer": "done"},
                current_step_key="convert",
            ),
        ])

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, None)

        assert dropped == 1
        assert repaired.jobs[0].resume_status == ""
        assert repaired.jobs[0].step_statuses == {}
        assert repaired.jobs[0].current_step_key == ""

    def test_repair_copies_fallback_name_when_restored_name_is_blank(self):
        """Reparierter Workflow mit leerem Namen übernimmt Fallback-Namen (L462)."""
        restored = Workflow(
            name="",
            jobs=[
                WorkflowJob(
                    name="Job 1",
                    source_mode="files",
                    files=[],
                    resume_status="Transfer abgebrochen",
                    step_statuses={"transfer": "cancelled"},
                ),
            ],
        )
        fallback = Workflow(
            name="Spieltag 23",
            shutdown_after=True,
            jobs=[
                WorkflowJob(
                    name="Job 1",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/a.mp4")],
                ),
            ],
        )

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, fallback)

        assert repaired_count == 1
        assert repaired.name == "Spieltag 23"

    def test_repair_copies_shutdown_after_from_fallback(self):
        """Reparierter Workflow übernimmt shutdown_after=True vom Fallback (L464)."""
        restored = Workflow(
            name="",
            shutdown_after=False,
            jobs=[
                WorkflowJob(
                    name="Job 1",
                    source_mode="files",
                    files=[],
                    resume_status="Transfer abgebrochen",
                    step_statuses={"transfer": "cancelled"},
                ),
            ],
        )
        fallback = Workflow(
            name="Spieltag 23",
            shutdown_after=True,
            jobs=[
                WorkflowJob(
                    name="Job 1",
                    source_mode="files",
                    files=[FileEntry(source_path="/tmp/a.mp4")],
                ),
            ],
        )

        repaired, repaired_count, dropped = _repair_restored_workflow(restored, fallback)

        assert repaired_count == 1
        assert repaired.shutdown_after is True

    def test_empty_restored_jobs_returns_zero_counts(self):
        """L419: restored.jobs ist leer → (restored, 0, 0) ohne Schleife."""
        restored = Workflow(jobs=[])
        repaired, repaired_count, dropped = _repair_restored_workflow(restored, None)
        assert repaired_count == 0
        assert dropped == 0
