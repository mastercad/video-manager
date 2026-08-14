"""Tests für das Workflow-Datenmodell (workflow.py).

Geprüft:
- FileEntry Standardwerte und merge_group_id-Feld
- WorkflowJob Serialisierung (to_dict / from_dict, Roundtrip)
- Workflow Serialisierung + Persistenz (save/load)
- Migration alter WorkflowSource-Formate in das neue WorkflowJob-Format
"""

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

# --- Modulimport (kein Qt nötig) ---
from src.workflow import (
    FileEntry,
    Workflow,
    WorkflowJob,
    _migrate_source_to_job,
    graph_node_branch_has_targets,
    graph_source_reaches_type,
)


# ─── FileEntry ────────────────────────────────────────────────────────────────

class TestFileEntry:
    def test_defaults(self):
        fe = FileEntry()
        assert fe.source_path == ""
        assert fe.output_filename == ""
        assert fe.youtube_title == ""
        assert fe.youtube_description == ""
        assert fe.youtube_playlist == ""
        assert fe.kaderblick_game_id == ""
        assert fe.kaderblick_game_start == 0
        assert fe.kaderblick_video_type_id == 0
        assert fe.kaderblick_camera_id == 0
        assert fe.merge_group_id == ""  # neu hinzugefügtes Feld

    def test_merge_group_id_is_set(self):
        fe = FileEntry(source_path="/foo/bar.mp4", merge_group_id="grp-abc")
        assert fe.merge_group_id == "grp-abc"

    def test_all_fields_survive_asdict(self):
        fe = FileEntry(
            source_path="/a/b.mp4",
            output_filename="b_out.mp4",
            youtube_title="Titel",
            youtube_description="Beschr.",
            youtube_playlist="Playlist",
            kaderblick_game_id="99",
            kaderblick_game_start=10,
            kaderblick_video_type_id=2,
            kaderblick_camera_id=3,
            merge_group_id="mg1",
        )
        d = asdict(fe)
        assert d["merge_group_id"] == "mg1"
        assert d["kaderblick_camera_id"] == 3


# ─── WorkflowJob ─────────────────────────────────────────────────────────────

class TestWorkflowJob:
    def test_id_is_generated(self):
        j1 = WorkflowJob()
        j2 = WorkflowJob()
        assert j1.id != j2.id
        assert len(j1.id) == 8

    def test_defaults(self):
        job = WorkflowJob()
        assert job.source_mode == "files"
        assert job.convert_enabled is True
        assert job.encoder == "auto"
        assert job.crf == 18
        assert job.fps == 25
        assert job.output_format == "mp4"
        assert job.upload_youtube is False
        assert job.upload_kaderblick is False
        assert job.merge_audio is False

    def test_to_dict_excludes_runtime_fields(self):
        job = WorkflowJob()
        job.resume_status = "Transfer OK"
        job.step_statuses = {"transfer": "done"}
        job.step_details = {"transfer": "Datei: a.mp4"}
        job.status = "Läuft"
        job.progress_pct = 42
        job.overall_progress_pct = 84
        job.current_step_key = "convert"
        job.error_msg = "test"
        job.transfer_status = "Transfer 1/3"
        job.transfer_progress_pct = 66
        job.run_started_at = "2026-03-25T10:00:00"
        job.run_finished_at = "2026-03-25T10:05:00"
        job.run_elapsed_seconds = 300.0
        d = job.to_dict()
        assert "resume_status" not in d
        assert "step_statuses" not in d
        assert "step_details" not in d
        assert "status" not in d
        assert "progress_pct" not in d
        assert "overall_progress_pct" not in d
        assert "current_step_key" not in d
        assert "error_msg" not in d
        assert "transfer_status" not in d
        assert "transfer_progress_pct" not in d
        assert "run_started_at" not in d
        assert "run_finished_at" not in d
        assert "run_elapsed_seconds" not in d

    def test_to_dict_can_include_resume_runtime_for_last_workflow(self):
        job = WorkflowJob(
            resume_status="Transfer OK",
            step_statuses={"transfer": "done", "convert": "running"},
            step_details={"convert": "Quelldatei: a.mp4"},
            progress_pct=42,
            overall_progress_pct=84,
            current_step_key="convert",
            transfer_status="Transfer OK",
            transfer_progress_pct=100,
            run_started_at="2026-03-25T10:00:00",
            run_finished_at="",
            run_elapsed_seconds=120.0,
        )

        d = job.to_dict(include_runtime=True)

        assert d["resume_status"] == "Transfer OK"
        assert d["step_statuses"] == {"transfer": "done", "convert": "running"}
        assert d["step_details"] == {"convert": "Quelldatei: a.mp4"}
        assert d["progress_pct"] == 42
        assert d["overall_progress_pct"] == 84
        assert d["current_step_key"] == "convert"
        assert d["transfer_status"] == "Transfer OK"
        assert d["transfer_progress_pct"] == 100
        assert d["run_started_at"] == "2026-03-25T10:00:00"
        assert d["run_elapsed_seconds"] == 120.0
        assert "status" not in d
        assert "error_msg" not in d

    def test_workflow_publish_session_is_only_persisted_in_last_workflow(self):
        workflow = Workflow(
            jobs=[WorkflowJob(name="Kamera")],
            started_job_ids=["job-1"],
            kaderblick_publish_statuses={"42": "error"},
        )

        assert "started_job_ids" not in workflow.to_dict()
        runtime = workflow.to_dict(include_runtime=True)
        restored = Workflow.from_dict(runtime, include_runtime=True)

        assert restored.started_job_ids == ["job-1"]
        assert restored.kaderblick_publish_statuses == {"42": "error"}

    def test_to_dict_contains_core_fields(self):
        job = WorkflowJob(name="Test", encoder="libx264", crf=22)
        d = job.to_dict()
        assert d["name"] == "Test"
        assert d["encoder"] == "libx264"
        assert d["crf"] == 22
        assert "source_mode" in d
        assert "files" in d

    def test_roundtrip_empty(self):
        job = WorkflowJob()
        restored = WorkflowJob.from_dict(job.to_dict())
        assert restored.id == job.id
        assert restored.source_mode == "files"
        assert restored.files == []

    def test_roundtrip_with_files(self):
        fe = FileEntry(source_path="/a/b.mp4", merge_group_id="grp1")
        job = WorkflowJob(
            name="Mit Dateien",
            source_mode="files",
            files=[fe],
            upload_youtube=True,
            default_youtube_playlist="Meine Playlist",
            default_youtube_competition="Sparkassenpokal",
        )
        d = job.to_dict()
        restored = WorkflowJob.from_dict(d)
        assert restored.name == "Mit Dateien"
        assert restored.upload_youtube is True
        assert restored.default_youtube_playlist == "Meine Playlist"
        assert restored.default_youtube_competition == "Sparkassenpokal"
        assert len(restored.files) == 1
        assert restored.files[0].source_path == "/a/b.mp4"
        assert restored.files[0].merge_group_id == "grp1"

    def test_roundtrip_pi_mode(self):
        job = WorkflowJob(
            source_mode="pi_download",
            device_name="Pi-Links",
            download_destination="/mnt/footage",
            delete_after_download=True,
        )
        restored = WorkflowJob.from_dict(job.to_dict())
        assert restored.source_mode == "pi_download"
        assert restored.device_name == "Pi-Links"
        assert restored.download_destination == "/mnt/footage"
        assert restored.delete_after_download is True

    def test_from_dict_ignores_unknown_keys(self):
        job = WorkflowJob()
        d = job.to_dict()
        d["nonexistent_field"] = "garbage"
        restored = WorkflowJob.from_dict(d)
        assert not hasattr(restored, "nonexistent_field")

    def test_from_dict_ignores_runtime_fields_even_if_present(self):
        job = WorkflowJob()
        d = job.to_dict()
        d["status"] = "Läuft"   # wurde von to_dict entfernt → manuell einfügen
        restored = WorkflowJob.from_dict(d)
        # Laufzeitfeld soll nicht zurückgeschrieben werden
        assert restored.status == "Wartend"  # Standardwert

    def test_resume_fields_are_persisted(self):
        job = WorkflowJob(
            resume_status="Transfer OK",
            step_statuses={"transfer": "done", "convert": "running"},
        )
        restored = WorkflowJob.from_dict(job.to_dict(include_runtime=True), include_runtime=True)
        assert restored.resume_status == "Transfer OK"
        assert restored.step_statuses == {
            "transfer": "done",
            "convert": "running",
        }

    def test_from_dict_ignores_resume_fields_for_shared_workflow_load(self):
        restored = WorkflowJob.from_dict(
            {
                "name": "Job A",
                "resume_status": "Transfer OK",
                "step_statuses": {"transfer": "done"},
                "progress_pct": 75,
                "current_step_key": "convert",
                "run_elapsed_seconds": 3600.0,
            }
        )

        assert restored.name == "Job A"
        assert restored.resume_status == ""
        assert restored.step_statuses == {}
        assert restored.progress_pct == 0
        assert restored.current_step_key == ""
        assert restored.run_elapsed_seconds == 0.0


# ─── Workflow ─────────────────────────────────────────────────────────────────

class TestWorkflow:
    def _make_workflow(self) -> Workflow:
        job = WorkflowJob(name="Job A", upload_youtube=True)
        return Workflow(name="Test-WF", job=job)

    def _make_multi_workflow(self) -> Workflow:
        return Workflow(
            name="Test-WF",
            jobs=[
                WorkflowJob(name="Job A", upload_youtube=True),
                WorkflowJob(name="Job B", upload_kaderblick=True),
            ],
        )

    def test_to_dict_structure(self):
        wf = self._make_workflow()
        d = wf.to_dict()
        assert d["name"] == "Test-WF"
        assert isinstance(d["job"], dict)
        assert d["job"]["name"] == "Job A"
        assert "last_run_started_at" not in d
        assert "last_run_finished_at" not in d
        assert "last_run_elapsed_seconds" not in d

    def test_to_dict_can_include_runtime_for_last_workflow_snapshot(self):
        wf = Workflow(
            name="Test-WF",
            job=WorkflowJob(name="Job A", resume_status="Transfer OK", step_statuses={"transfer": "done"}),
            last_run_started_at="2026-03-25T10:00:00",
            last_run_finished_at="2026-03-25T10:05:00",
            last_run_elapsed_seconds=300.0,
        )

        d = wf.to_dict(include_runtime=True)

        assert d["last_run_started_at"] == "2026-03-25T10:00:00"
        assert d["last_run_finished_at"] == "2026-03-25T10:05:00"
        assert d["last_run_elapsed_seconds"] == 300.0
        assert d["job"]["resume_status"] == "Transfer OK"

    def test_roundtrip(self):
        wf = self._make_workflow()
        restored = Workflow.from_dict(wf.to_dict())
        assert restored.name == "Test-WF"
        assert restored.job is not None
        assert restored.job.upload_youtube is True

    def test_roundtrip_preserves_all_jobs(self):
        wf = self._make_multi_workflow()

        restored = Workflow.from_dict(wf.to_dict())

        assert [job.name for job in restored.jobs] == ["Job A", "Job B"]
        assert restored.jobs[0].upload_youtube is True
        assert restored.jobs[1].upload_kaderblick is True

    def test_save_and_load(self):
        wf = self._make_workflow()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.json"
            wf.save(path)
            assert path.exists()
            loaded = Workflow.load(path)
        assert loaded.name == "Test-WF"
        assert loaded.job is not None
        assert loaded.job.name == "Job A"

    def test_save_and_load_preserves_multiple_jobs(self):
        wf = self._make_multi_workflow()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.json"
            wf.save(path)
            loaded = Workflow.load(path)

        assert [job.name for job in loaded.jobs] == ["Job A", "Job B"]

    def test_save_and_load_shared_workflow_strips_resume_state(self):
        wf = Workflow(
            name="Test-WF",
            job=WorkflowJob(
                name="Job A",
                source_mode="files",
                files=[FileEntry(source_path="/tmp/a.mp4")],
                resume_status="Transfer OK",
                step_statuses={"transfer": "done", "convert": "running"},
                progress_pct=42,
                current_step_key="convert",
                run_elapsed_seconds=120.0,
            ),
            last_run_started_at="2026-03-25T10:00:00",
            last_run_finished_at="2026-03-25T10:05:00",
            last_run_elapsed_seconds=300.0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.json"
            wf.save(path)
            loaded = Workflow.load(path)
            raw = path.read_text(encoding="utf-8")

        assert '"resume_status"' not in raw
        assert '"step_statuses"' not in raw
        assert '"progress_pct"' not in raw
        assert '"current_step_key"' not in raw
        assert '"last_run_elapsed_seconds"' not in raw
        assert loaded.job is not None
        assert loaded.job.resume_status == ""
        assert loaded.job.step_statuses == {}
        assert loaded.job.progress_pct == 0
        assert loaded.job.current_step_key == ""
        assert loaded.job.run_elapsed_seconds == 0.0
        assert loaded.last_run_elapsed_seconds == 0.0

    def test_save_creates_parent_dirs(self):
        wf = self._make_workflow()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "dir" / "wf.json"
            wf.save(path)
            assert path.exists()
            loaded = Workflow.load(path)
        assert loaded.job is not None

    def test_shutdown_after_defaults_false(self):
        wf = Workflow()
        assert wf.shutdown_after is False
        assert wf.publish_kaderblick_videos is False
        d = wf.to_dict()
        assert d["shutdown_after"] is False
        assert d["publish_kaderblick_videos"] is False

    def test_publish_kaderblick_option_roundtrip(self):
        wf = Workflow(publish_kaderblick_videos=True)

        restored = Workflow.from_dict(wf.to_dict())

        assert restored.publish_kaderblick_videos is True

    def test_json_valid_utf8(self):
        wf = Workflow(name="Umlaut: äöü ß", job=WorkflowJob(name="Auftrag: Þórsmörk"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            wf.save(path)
            raw = path.read_text(encoding="utf-8")
            # ensure_ascii=False → Umlaute werden direkt gespeichert
            assert "äöü" in raw
            assert "Þórsmörk" in raw

    def test_branch_aware_reachability_filters_validation_outputs(self):
        job = WorkflowJob(
            name="Validation Graph",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/a.mp4", graph_source_id="source-files-1")],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "validate-1", "type": "validate_surface"},
                {"id": "repair-1", "type": "repair"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "validate-1"},
                {"source": "validate-1", "target": "repair-1", "branch": "repairable"},
                {"source": "validate-1", "target": "yt-1", "branch": "ok"},
                {"source": "repair-1", "target": "yt-1"},
            ],
        )

        assert graph_source_reaches_type(job, "source-files-1", "repair") is True
        assert graph_source_reaches_type(job, "source-files-1", "repair", {"validate-1": "ok"}) is False
        assert graph_source_reaches_type(job, "source-files-1", "repair", {"validate-1": "repairable"}) is True
        assert graph_source_reaches_type(job, "source-files-1", "yt_version", {"validate-1": "ok"}) is True

    def test_validation_branch_target_detection_respects_connected_outputs(self):
        job = WorkflowJob(
            name="Validation Graph",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/a.mp4", graph_source_id="source-files-1")],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "validate-1", "type": "validate_surface"},
                {"id": "repair-1", "type": "repair"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "validate-1"},
                {"source": "validate-1", "target": "repair-1", "branch": "repairable"},
            ],
        )

        assert graph_node_branch_has_targets(job, "validate-1", "repairable") is True
        assert graph_node_branch_has_targets(job, "validate-1", "irreparable") is False


# ─── Migration ────────────────────────────────────────────────────────────────

class TestMigration:
    """Alte WorkflowSource-Formate müssen nahtlos migriert werden."""

    def _old_source_pi(self) -> dict:
        return {
            "id": "aa001122",
            "enabled": True,
            "name": "Kamera Links",
            "source_type": "pi_camera",
            "source_path": "",
            "device_name": "pi-links",
            "destination_path": "/footage",
            "delete_source": True,
            "encoder": "libx264",
            "crf": 20,
            "preset": "fast",
            "fps": 30,
            "output_format": "mp4",
            "merge_audio_video": True,
            "upload_youtube": True,
            "youtube_title": "Test Titel",
            "youtube_playlist": "Meine PL",
        }

    def test_pi_migration_mode(self):
        job_dict = _migrate_source_to_job(self._old_source_pi())
        assert job_dict["source_mode"] == "pi_download"
        assert job_dict["device_name"] == "pi-links"
        assert job_dict["download_destination"] == "/footage"
        assert job_dict["delete_after_download"] is True

    def test_pi_migration_encoding(self):
        job_dict = _migrate_source_to_job(self._old_source_pi())
        assert job_dict["encoder"] == "libx264"
        assert job_dict["crf"] == 20
        assert job_dict["preset"] == "fast"

    def test_pi_migration_youtube(self):
        job_dict = _migrate_source_to_job(self._old_source_pi())
        assert job_dict["upload_youtube"] is True
        assert job_dict["default_youtube_title"] == "Test Titel"
        assert job_dict["default_youtube_playlist"] == "Meine PL"

    def test_old_format_loads_via_from_dict(self):
        """Workflow.from_dict verarbeitet altes sources-Format korrekt."""
        old_data = {
            "name": "Altes WF",
            "shutdown_after": False,
            "sources": [self._old_source_pi()],
        }
        wf = Workflow.from_dict(old_data)
        assert wf.job is not None
        assert wf.job.source_mode == "pi_download"

    def test_folder_scan_migration(self):
        source = {
            "source_type": "local",
            "source_path": "/media/videos",
            "destination_path": "/media/converted",
            "move_to_destination": True,
            "file_extensions": "*.mjpg",
            "encoder": "auto",
            "crf": 18,
            "preset": "medium",
            "fps": 25,
            "output_format": "mp4",
        }
        job_dict = _migrate_source_to_job(source)
        assert job_dict["source_mode"] == "folder_scan"
        assert job_dict["source_folder"] == "/media/videos"
        assert job_dict["move_files"] is True
        assert job_dict["file_pattern"] == "*.mjpg"
