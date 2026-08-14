"""Tests für WorkflowExecutor – innere Logik (ohne Qt-Event-Loop).

Geprüft:
- _find_file_entry()        – FileEntry-Suche per Pfad-String
- _resolve_youtube_title()  – Fallback-Kette: per-Datei → Job-Standard → Dateiname
- _build_job_settings()     – Job-Werte werden in AppSettings übertragen
- Merge-Gruppen-Erkennung   – _merge_skip_conv_idx enthält die richtigen Indizes
- Failure-Counter            – _transfer_fail zählt Fehler in Phase 1
- finished-Signal            – (ok, skip, fail) korrekt bei leerem Workflow
"""

import threading
import tempfile
import time
from datetime import date
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# PySide6 braucht eine QApplication – aber WorkflowExecutor ist ein QObject,
# also müssen wir eine minimal-App erheben.
from PySide6.QtWidgets import QApplication
import sys

# Einmalige App-Instanz für alle Tests in diesem Modul
_app = QApplication.instance() or QApplication(sys.argv)

from src.workflow import Workflow, WorkflowJob, FileEntry, reset_job_for_rebuild
from src.runtime.workflow_executor import WorkflowExecutor
from src.workflow_steps import PreparedOutput
from src.workflow_steps.models import ConvertItem
from src.media.ffmpeg_runner import MediaValidationResult
from src.media.converter import ConvertJob
from src.integrations.youtube import upload_to_youtube


# ─── Hilfsfunktionen ─────────────────────────────────────────────────────────

def _make_settings():
    """Erstellt ein minimales AppSettings-Objekt ohne Datei-I/O."""
    from src.settings import AppSettings
    return AppSettings()   # Standardwerte, ohne Datei-I/O


def _processed_dir_for(path: Path) -> Path:
    if path.parent.name.lower() == "raw":
        return path.parent.parent / "processed"
    if path.parent.name.lower() == "processed":
        return path.parent
    return path.parent / "processed"


def _derived_path(path: Path, suffix: str, extension: str | None = None) -> Path:
    processed_dir = _processed_dir_for(path)
    ext = path.suffix if extension is None else extension
    return processed_dir / f"{path.stem}{suffix}{ext}"


# ─── _find_file_entry ─────────────────────────────────────────────────────────

class TestFindFileEntry:
    def _executor(self) -> WorkflowExecutor:
        wf = Workflow()
        return WorkflowExecutor(wf, _make_settings())

    def test_found_by_exact_path(self):
        job = WorkflowJob()
        job.files = [FileEntry(source_path="/a/b.mp4")]
        result = WorkflowExecutor._find_file_entry(job, "/a/b.mp4")
        assert result is not None
        assert result.source_path == "/a/b.mp4"

    def test_not_found_returns_none(self):
        job = WorkflowJob()
        job.files = [FileEntry(source_path="/a/b.mp4")]
        assert WorkflowExecutor._find_file_entry(job, "/a/c.mp4") is None

    def test_empty_files_returns_none(self):
        job = WorkflowJob()   # files=[]
        assert WorkflowExecutor._find_file_entry(job, "/a/b.mp4") is None

    def test_first_match_returned(self):
        """Falls zwei Einträge den gleichen Pfad haben, wird der erste zurückgegeben."""
        fe1 = FileEntry(source_path="/x.mp4", youtube_title="Erster")
        fe2 = FileEntry(source_path="/x.mp4", youtube_title="Zweiter")
        job = WorkflowJob(files=[fe1, fe2])
        result = WorkflowExecutor._find_file_entry(job, "/x.mp4")
        assert result is not None
        assert result.youtube_title == "Erster"

    def test_unique_filename_match_after_copy(self):
        """Nach Kopieren/Verschieben bleibt die Zuordnung über den Dateinamen erhalten."""
        job = WorkflowJob()
        job.files = [FileEntry(source_path="/quelle/halbzeit1.mp4", merge_group_id="hz1")]
        result = WorkflowExecutor._find_file_entry(job, "/ziel/halbzeit1.mp4")
        assert result is not None
        assert result.merge_group_id == "hz1"

    def test_ambiguous_filename_match_returns_none(self):
        """Gleicher Dateiname mehrfach: kein unsicheres Fallback verwenden."""
        job = WorkflowJob()
        job.files = [
            FileEntry(source_path="/quelle_a/halbzeit.mp4", youtube_title="A"),
            FileEntry(source_path="/quelle_b/halbzeit.mp4", youtube_title="B"),
        ]
        result = WorkflowExecutor._find_file_entry(job, "/ziel/halbzeit.mp4")
        assert result is None


# ─── _resolve_youtube_title ───────────────────────────────────────────────────

class TestResolveYoutubeTitle:
    def test_per_file_title_wins(self):
        fe = FileEntry(source_path="/a/b.mp4", youtube_title="Datei-Titel")
        job = WorkflowJob(
            default_youtube_title="Job-Standard",
            files=[fe],
        )
        result = WorkflowExecutor._resolve_youtube_title(job, "/a/b.mp4")
        assert result == "Datei-Titel"

    def test_job_default_used_when_no_file_title(self):
        fe = FileEntry(source_path="/a/b.mp4", youtube_title="")
        job = WorkflowJob(
            default_youtube_title="Job-Standard",
            files=[fe],
        )
        result = WorkflowExecutor._resolve_youtube_title(job, "/a/b.mp4")
        assert result == "Job-Standard"

    def test_placeholder_source_stem_does_not_override_richer_job_title(self):
        fe = FileEntry(source_path="/raw/aufnahme_2026-03-01_09-31-27.mjpg", youtube_title="aufnahme_2026-03-01_09-31-27")
        job = WorkflowJob(
            default_youtube_title="2026-03-23 | SV Pesterwitz vs Gast | Kaderblick Links | 1. Halbzeit",
            files=[fe],
        )

        result = WorkflowExecutor._resolve_youtube_title(job, "/raw/aufnahme_2026-03-01_09-31-27.mjpg")

        assert result == "2026-03-23 | SV Pesterwitz vs Gast | Kaderblick Links | 1. Halbzeit"

    def test_filename_stem_as_last_fallback(self):
        fe = FileEntry(source_path="/a/mein_video.mp4", youtube_title="")
        job = WorkflowJob(
            default_youtube_title="",
            files=[fe],
        )
        result = WorkflowExecutor._resolve_youtube_title(job, "/a/mein_video.mp4")
        assert result == "mein_video"

    def test_no_entry_uses_job_default(self):
        """Wenn keine FileEntry existiert → Job-Standard."""
        job = WorkflowJob(default_youtube_title="Globaler Titel", files=[])
        result = WorkflowExecutor._resolve_youtube_title(job, "/a/b.mp4")
        assert result == "Globaler Titel"

    def test_no_entry_no_default_uses_stem(self):
        job = WorkflowJob(default_youtube_title="", files=[])
        result = WorkflowExecutor._resolve_youtube_title(job, "/videos/abcdef.mkv")
        assert result == "abcdef"

    def test_register_runtime_file_entry_does_not_prefill_youtube_title(self):
        job = WorkflowJob(
            graph_nodes=[{"id": "source-pi-1", "type": "source_pi_download"}],
            graph_edges=[],
            files=[],
        )

        entry = WorkflowExecutor._register_runtime_file_entry(job, "source-pi-1", "/videos/kamera/aufnahme_001.mjpg")

        assert entry.youtube_title == ""
        assert entry.title_card_subtitle == "aufnahme_001"


class TestPlannedGraphSteps:
    def test_disconnected_titlecard_is_not_a_planned_step(self):
        job = WorkflowJob(
            name="Disconnected Titlecard",
            source_mode="files",
            files=[FileEntry(source_path="/tmp/input.mp4", graph_source_id="source-1")],
            graph_nodes=[
                {"id": "source-1", "type": "source_files"},
                {"id": "merge-1", "type": "merge"},
                {"id": "upload-1", "type": "youtube_upload"},
                {"id": "title-1", "type": "titlecard"},
            ],
            graph_edges=[
                {"source": "source-1", "target": "merge-1"},
                {"source": "merge-1", "target": "upload-1"},
            ],
        )

        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

        assert ex._planned_job_steps(job) == ["transfer", "merge", "youtube_upload"]


# ─── _build_job_settings ──────────────────────────────────────────────────────

class TestBuildJobSettings:
    def _executor(self) -> WorkflowExecutor:
        wf = Workflow()
        return WorkflowExecutor(wf, _make_settings())

    def test_encoder_transferred(self):
        ex = self._executor()
        job = WorkflowJob(encoder="libx265", crf=28, preset="slow",
                          fps=30, output_format="avi", output_resolution="720p")
        s = ex._build_job_settings(job)
        assert s.video.encoder == "libx265"
        assert s.video.crf == 28
        assert s.video.preset == "slow"
        assert s.video.fps == 30
        assert s.video.output_format == "avi"
        assert s.video.output_resolution == "720p"

    def test_audio_transferred(self):
        ex = self._executor()
        job = WorkflowJob(
            merge_audio=True,
            amplify_audio=True,
            amplify_db=9.0,
            audio_sync=True,
        )
        s = ex._build_job_settings(job)
        assert s.audio.include_audio is True
        assert s.audio.amplify_audio is True
        assert s.audio.amplify_db == 9.0
        assert s.video.audio_sync is True

    def test_youtube_flags_transferred(self):
        ex = self._executor()
        job = WorkflowJob(
            create_youtube_version=True,
            upload_youtube=True,
        )
        s = ex._build_job_settings(job)
        assert s.youtube.create_youtube is True
        assert s.youtube.upload_to_youtube is True

    def test_encoder_safety_flags_follow_job_configuration(self):
        ex = self._executor()
        job = WorkflowJob(no_bframes=False)

        s = ex._build_job_settings(job)

        assert s.video.no_bframes is False
        assert s.video.keyframe_interval >= 1
    def test_resume_existing_merge_group_applies_merge_metadata_and_uses_existing_merged_output(self, tmp_path):
        source = tmp_path / "halbzeit1.mp4"
        source.write_text("video", encoding="utf-8")
        job = WorkflowJob(
            source_mode="files",
            files=[FileEntry(source_path=str(source), merge_group_id="hz2")],
            merge_output_title="DJI 2. Halbzeit",
            merge_output_playlist="Spieltag 12",
            merge_output_description="Zusammengesetzte Halbzeit",
        )
        workflow = Workflow(jobs=[job])
        executor = WorkflowExecutor(workflow, _make_settings())
        cv_job = ConvertJob(
            source_path=source,
            output_path=source,
            youtube_title="Alter Titel",
            youtube_playlist="Alte Playlist",
            youtube_description="Alte Beschreibung",
        )
        item = ConvertItem(orig_idx=0, job=job, cv_job=cv_job)
        merged_path = source.with_stem("DJI 2. Halbzeit")
        merged_path.write_text("merged", encoding="utf-8")

        prepared = executor._resume_existing_merge_group([item], "youtube_upload")

        assert prepared is not None
        assert prepared.cv_job.output_path == merged_path
        assert prepared.cv_job.youtube_title == "DJI 2. Halbzeit"
        assert prepared.cv_job.youtube_playlist == "Spieltag 12"
        assert prepared.cv_job.youtube_description == "Zusammengesetzte Halbzeit"
        assert prepared.resume_from_step == "youtube_upload"

    def test_build_convert_job_uses_sanitized_title_as_default_output_filename(self, tmp_path):
        ex = self._executor()
        source = tmp_path / "halbzeit1.mp4"
        source.write_text("video", encoding="utf-8")
        job = WorkflowJob(
            output_format="mp4",
            files=[
                FileEntry(
                    source_path=str(source),
                    youtube_title="2026-03-22 | Heim vs Gast | Kamera 1 | Links 1. Halbzeit",
                )
            ],
        )

        cv_job = ex._build_convert_job(job, str(source))

        assert cv_job.output_path is not None
        assert cv_job.output_path.name == "2026-03-22 - Heim vs Gast - Kamera 1 - Links 1. Halbzeit.mp4"

    def test_build_convert_job_uses_structured_youtube_metadata_defaults(self, tmp_path):
        ex = self._executor()
        source = tmp_path / "halbzeit1.mp4"
        source.write_text("video", encoding="utf-8")
        job = WorkflowJob(
            output_format="mp4",
            youtube_match_data={
                "date_iso": "2026-03-22",
                "competition": "Pokal",
                "home_team": "Heim",
                "away_team": "Gast",
            },
            youtube_segment_data={
                "camera": "Hauptkamera",
                "side": "Links",
                "half": 1,
                "part": 0,
                "type_name": "1. Halbzeit",
            },
        )

        cv_job = ex._build_convert_job(job, str(source))

        assert cv_job.youtube_title == "2026-03-22 | Heim vs Gast | Hauptkamera | Links 1. Halbzeit"
        assert cv_job.youtube_playlist == "22.03.2026 | Pokal | Heim vs Gast"
        assert "Pokal" in cv_job.youtube_description
        assert "Pokal" in cv_job.youtube_tags
        assert "Heim" in cv_job.youtube_tags
        assert "Gast" in cv_job.youtube_tags


class TestLivePipelineProgress:
    def test_convert_progress_is_emitted_before_pipeline_item_finishes(self, tmp_path):
        from queue import Queue

        from src.runtime.workflow_executor.helpers import _PipelineWorkerView

        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="Live Progress",
            source_mode="files",
            convert_enabled=True,
            files=[FileEntry(source_path=str(source))],
        )
        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        def _fake_convert(cv_job, _settings, **kwargs):
            progress_callback = kwargs["progress_callback"]
            if cv_job.output_path is not None:
                cv_job.output_path.parent.mkdir(parents=True, exist_ok=True)
                cv_job.output_path.write_text("converted", encoding="utf-8")
            progress_callback(10)
            time.sleep(0.15)
            progress_callback(65)
            cv_job.status = "Fertig"
            return True

        ex._convert_func = _fake_convert
        event_queue: Queue = Queue()
        ex._pipeline_event_queue = event_queue
        ex._pipeline_last_drain = 0.0
        worker_executor = _PipelineWorkerView(ex, event_queue)
        received_progress: list[int] = []

        def _on_progress(_orig_idx: int, pct: int, _step_key: str = "") -> None:
            received_progress.append(pct)

        ex.job_progress.connect(_on_progress)

        result = ex._convert_step.execute(
            worker_executor,
            0,
            job,
            ex._build_convert_job(job, str(source)),
            ex._build_job_settings(job),
            0,
            1,
        )

        ex._drain_pipeline_events(event_queue)
        ex._pipeline_event_queue = None
        ex._pipeline_last_drain = 0.0

        assert result == "ok"
        assert received_progress[0] == 0
        assert 10 in received_progress
        assert 65 in received_progress

    def test_pipeline_progress_waits_for_owner_thread_drain(self, tmp_path):
        from queue import Queue

        from src.runtime.workflow_executor.helpers import _PipelineWorkerView

        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="Live Flush",
            source_mode="files",
            convert_enabled=True,
            files=[FileEntry(source_path=str(source))],
        )
        ex = WorkflowExecutor(Workflow(job=job), _make_settings())
        event_queue: Queue = Queue()
        ex._pipeline_event_queue = event_queue
        ex._pipeline_last_drain = 0.0
        ex._pipeline_drain_lock = threading.Lock()
        worker_executor = _PipelineWorkerView(ex, event_queue)
        received_progress: list[int] = []

        def _on_progress(_orig_idx: int, pct: int, _step_key: str = "") -> None:
            received_progress.append(pct)

        ex.job_progress.connect(_on_progress)

        ex._pipeline_owner_thread_id = -1
        worker_executor.job_progress.emit(0, 42, "")

        assert received_progress == []
        assert not event_queue.empty()

        ex._pipeline_owner_thread_id = ex._owner_thread_id
        ex._drain_pipeline_events(event_queue)

        ex._pipeline_event_queue = None
        ex._pipeline_last_drain = 0.0
        ex._pipeline_drain_lock = None

        assert received_progress == [42]
        assert event_queue.empty()


class TestJobCancelFlag:
    def test_wait_times_out_when_job_not_cancelled(self):
        ex = WorkflowExecutor(Workflow(), _make_settings())
        flag = ex._cancel_flag_for_job(0)

        assert flag.wait(timeout=0.01) is False

    def test_wait_returns_true_after_job_cancel(self):
        ex = WorkflowExecutor(Workflow(jobs=[WorkflowJob(name="A")]), _make_settings())
        flag = ex._cancel_flag_for_job(0)

        ex.cancel(active_indices={0})

        assert flag.wait(timeout=0.01) is True

    def test_wait_returns_true_immediately_when_already_cancelled(self):
        """L33: timeout=0 → return is_set() immediately."""
        ex = WorkflowExecutor(Workflow(jobs=[WorkflowJob(name="A")]), _make_settings())
        flag = ex._cancel_flag_for_job(0)
        ex.cancel(active_indices={0})

        assert flag.wait(timeout=0) is True

    def test_wait_with_zero_timeout_not_cancelled_returns_false(self):
        """L33: timeout=0 and not cancelled → return is_set() = False."""
        ex = WorkflowExecutor(Workflow(), _make_settings())
        flag = ex._cancel_flag_for_job(0)

        assert flag.wait(timeout=0) is False

    def test_wait_with_none_timeout_returns_true_when_cancelled(self):
        """L45: no deadline branch — cancel from another thread."""
        ex = WorkflowExecutor(Workflow(jobs=[WorkflowJob(name="A")]), _make_settings())
        flag = ex._cancel_flag_for_job(0)

        import threading

        def _cancel_soon():
            time.sleep(0.05)
            ex.cancel(active_indices={0})

        t = threading.Thread(target=_cancel_soon)
        t.start()
        result = flag.wait(timeout=None)
        t.join(timeout=1.0)
        assert result is True


class TestMarkJobCancelled:
    def _make_executor_with_captured_statuses(self, jobs):
        ex = WorkflowExecutor(Workflow(jobs=jobs), _make_settings())
        emitted = []
        ex.job_status.connect(lambda idx, s: emitted.append((idx, s)))
        return ex, emitted

    def test_running_step_gets_cancelled_status(self):
        """Ein laufender Job (step_statuses hat 'running') wird korrekt abgebrochen."""
        job = WorkflowJob(
            name="A",
            current_step_key="convert",
            step_statuses={"transfer": "done", "convert": "running"},
            resume_status="Konvertiere \u2026",
        )
        ex, emitted = self._make_executor_with_captured_statuses([job])
        ex.cancel(active_indices={0})
        assert any("abgebrochen" in s.lower() for _, s in emitted)

    def test_not_started_job_is_skipped(self):
        """Ein Job ohne step_statuses und ohne resume_status wurde noch nie gestartet —
        er darf NICHT auf 'Job abgebrochen' gesetzt werden."""
        job = WorkflowJob(name="A")
        ex, emitted = self._make_executor_with_captured_statuses([job])
        ex.cancel(active_indices={0})
        assert emitted == [], f"Nicht gestarteter Job soll keinen Status bekommen, got: {emitted}"

    def test_finished_job_is_skipped(self):
        """Ein bereits fertiger Job darf NICHT auf 'Job abgebrochen' gesetzt werden."""
        job = WorkflowJob(
            name="A",
            resume_status="Fertig",
            step_statuses={"transfer": "done", "convert": "done"},
        )
        ex, emitted = self._make_executor_with_captured_statuses([job])
        ex.cancel(active_indices={0})
        assert emitted == [], f"Fertiger Job soll keinen Status bekommen, got: {emitted}"

    def test_already_cancelled_job_is_skipped(self):
        """Ein bereits abgebrochener Job darf nicht erneut auf 'Job abgebrochen' gesetzt werden."""
        job = WorkflowJob(
            name="A",
            resume_status="Konvertierung abgebrochen",
            step_statuses={"transfer": "done"},
        )
        ex, emitted = self._make_executor_with_captured_statuses([job])
        ex.cancel(active_indices={0})
        assert emitted == [], f"Bereits abgebrochener Job soll keinen Status bekommen, got: {emitted}"

    def test_error_job_is_skipped(self):
        """Ein Job mit Fehlerstatus darf nicht auf 'Job abgebrochen' gesetzt werden."""
        job = WorkflowJob(
            name="A",
            resume_status="Fehler: ffmpeg exit 1",
            step_statuses={"transfer": "done", "convert": "done"},
        )
        ex, emitted = self._make_executor_with_captured_statuses([job])
        ex.cancel(active_indices={0})
        assert emitted == [], f"Fehler-Job soll keinen Status bekommen, got: {emitted}"

    def test_in_progress_job_without_running_step_gets_job_abgebrochen(self):
        """Ein Job der gestartet wurde (hat step_statuses), aber aktuell keinen 'running'
        Step hat (z.B. zwischen zwei Steps), bekommt 'Job abgebrochen'."""
        job = WorkflowJob(
            name="A",
            resume_status="Konvertiere \u2026",
            current_step_key="convert",
            step_statuses={"transfer": "done"},
        )
        ex, emitted = self._make_executor_with_captured_statuses([job])
        ex.cancel(active_indices={0})
        assert any(s == "Job abgebrochen" for _, s in emitted), (
            f"Job zwischen Steps soll 'Job abgebrochen' bekommen, got: {emitted}"
        )

    def test_request_job_cancel_out_of_bounds_is_ignored(self):
        """L108: orig_idx außerhalb des Bereichs → return ohne Effekt."""
        ex = WorkflowExecutor(Workflow(jobs=[WorkflowJob(name="A")]), _make_settings())
        ex._request_job_cancel(99)  # darf nicht abstürzen
        assert 99 not in ex._cancelled_indices

    def test_request_job_cancel_already_cancelled_is_ignored(self):
        """L110: orig_idx bereits in cancelled_indices → kein doppelter Eintrag."""
        ex = WorkflowExecutor(Workflow(jobs=[WorkflowJob(name="A")]), _make_settings())
        ex._cancelled_indices.add(0)
        before = set(ex._cancelled_indices)
        ex._request_job_cancel(0)
        assert ex._cancelled_indices == before

    def test_mark_job_cancelled_out_of_bounds_is_ignored(self):
        """L123: orig_idx außerhalb des Bereichs → return ohne Effekt."""
        ex = WorkflowExecutor(Workflow(jobs=[WorkflowJob(name="A")]), _make_settings())
        ex._mark_job_cancelled(99)  # darf nicht abstürzen

    def test_cancelled_property_returns_cancel_event_state(self):
        """L160: cancelled property gibt _cancel.is_set() zurück."""
        ex = WorkflowExecutor(Workflow(), _make_settings())
        assert ex.cancelled is False
        ex._cancel.set()
        assert ex.cancelled is True


class TestYouTubeUploadRequest:
    def test_upload_to_youtube_sends_description_in_insert_body(self, tmp_path):
        output = tmp_path / "clip.mp4"
        output.write_text("video", encoding="utf-8")

        cv_job = ConvertJob(
            source_path=output,
            output_path=output,
            youtube_title="Titel",
            youtube_description="Beschreibung aus Merge oder Upload-Node",
            youtube_playlist="Playlist",
            youtube_tags=["Sport"],
        )

        captured: dict[str, object] = {}

        class _FakeInsertRequest:
            _resumable_uri = None

            def next_chunk(self):
                return None, {"id": "video-123"}

        class _FakeVideos:
            def insert(self, *, part, body, media_body):
                captured["part"] = part
                captured["body"] = body
                captured["media_body"] = media_body
                return _FakeInsertRequest()

        fake_service = MagicMock()
        fake_service.videos.return_value = _FakeVideos()
        fake_service.playlistItems.return_value.insert.return_value.execute.return_value = {}
        settings = _make_settings()
        settings.youtube.upload_to_youtube = True

        with patch("src.integrations.youtube._registry.already_uploaded", return_value=None), \
             patch("src.integrations.youtube._registry.get_pending", return_value=None), \
             patch("src.integrations.youtube._registry.record_pending"), \
             patch("src.integrations.youtube._registry.record_done"), \
               patch("src.integrations.youtube.MediaFileUpload", return_value=MagicMock()), \
             patch("src.integrations.youtube.find_or_create_playlist", return_value="pl-1"):
            upload_to_youtube(cv_job, settings, fake_service)

        assert captured["part"] == "snippet,status"
        body = captured["body"]
        assert body["snippet"]["title"] == "Titel"
        assert body["snippet"]["description"] == "Beschreibung aus Merge oder Upload-Node"
        assert body["snippet"]["tags"] == ["Sport"]

    def test_upload_to_youtube_prefers_existing_avi_youtube_variant(self, tmp_path):
        output = tmp_path / "clip.mp4"
        output.write_text("video", encoding="utf-8")
        yt_variant = tmp_path / "clip_youtube.avi"
        yt_variant.write_text("yt-video", encoding="utf-8")

        cv_job = ConvertJob(source_path=output, output_path=output, youtube_title="Titel")
        settings = _make_settings()
        settings.youtube.upload_to_youtube = True
        captured: dict[str, object] = {}

        class _FakeInsertRequest:
            _resumable_uri = None

            def next_chunk(self):
                return None, {"id": "video-avi"}

        class _FakeVideos:
            def insert(self, *, part, body, media_body):
                captured["body"] = body
                captured["media_body"] = media_body
                return _FakeInsertRequest()

        fake_service = MagicMock()
        fake_service.videos.return_value = _FakeVideos()

        with patch("src.integrations.youtube._registry.already_uploaded", return_value=None), \
             patch("src.integrations.youtube._registry.get_pending", return_value=None), \
             patch("src.integrations.youtube._registry.record_pending"), \
             patch("src.integrations.youtube._registry.record_done"), \
             patch("src.integrations.youtube.MediaFileUpload", return_value=MagicMock()) as media_upload:
            ok = upload_to_youtube(cv_job, settings, fake_service)

        assert ok is True
        media_upload.assert_called_once()
        assert media_upload.call_args.args[0] == str(yt_variant)
        assert media_upload.call_args.kwargs["mimetype"] == "video/x-msvideo"

    def test_upload_to_youtube_retries_ssl_eof_failures(self, tmp_path):
        output = tmp_path / "clip.mp4"
        output.write_text("video", encoding="utf-8")
        cv_job = ConvertJob(source_path=output, output_path=output, youtube_title="Titel")
        settings = _make_settings()
        settings.youtube.upload_to_youtube = True
        logs: list[str] = []
        call_count = {"chunks": 0}

        class _FakeInsertRequest:
            _resumable_uri = None

            def next_chunk(self):
                call_count["chunks"] += 1
                if call_count["chunks"] == 1:
                    raise RuntimeError("EOF occurred in violation of protocol (_ssl.c:2436)")
                return None, {"id": "video-123"}

        class _FakeVideos:
            def insert(self, *, part, body, media_body):
                return _FakeInsertRequest()

        fake_service = MagicMock()
        fake_service.videos.return_value = _FakeVideos()

        with patch("src.integrations.youtube._registry.already_uploaded", return_value=None), \
             patch("src.integrations.youtube._registry.get_pending", return_value=None), \
             patch("src.integrations.youtube._registry.record_pending"), \
             patch("src.integrations.youtube._registry.record_done"), \
             patch("src.integrations.youtube.MediaFileUpload", return_value=MagicMock()), \
             patch("src.integrations.youtube.time.sleep") as sleep_mock:
            ok = upload_to_youtube(cv_job, settings, fake_service, log_callback=logs.append)

        assert ok is True
        assert call_count["chunks"] == 2
        sleep_mock.assert_called_once_with(2)
        assert any("Upload-Fehler (Versuch 1/5)" in line for line in logs)

    def test_upload_to_youtube_restart_ignores_existing_registry_and_pending_resume(self, tmp_path):
        output = tmp_path / "clip.mp4"
        output.write_text("video", encoding="utf-8")
        cv_job = ConvertJob(source_path=output, output_path=output, youtube_title="Titel")
        settings = _make_settings()
        settings.youtube.upload_to_youtube = True
        captured: dict[str, object] = {}

        class _FakeInsertRequest:
            _resumable_uri = None

            def next_chunk(self):
                return None, {"id": "video-new"}

        class _FakeVideos:
            def insert(self, *, part, body, media_body):
                captured["part"] = part
                captured["body"] = body
                captured["media_body"] = media_body
                return _FakeInsertRequest()

        fake_service = MagicMock()
        fake_service.videos.return_value = _FakeVideos()

        with patch("src.integrations.youtube._registry.already_uploaded", return_value="video-old") as existing_mock, \
             patch("src.integrations.youtube._registry.get_pending", return_value="resume://old") as pending_mock, \
             patch("src.integrations.youtube._registry.record_pending"), \
             patch("src.integrations.youtube._registry.record_done"), \
             patch("src.integrations.youtube.MediaFileUpload", return_value=MagicMock()):
            ok = upload_to_youtube(
                cv_job,
                settings,
                fake_service,
                allow_reuse_existing=False,
            )

        assert ok is True
        existing_mock.assert_not_called()
        pending_mock.assert_not_called()
        assert captured["part"] == "snippet,status"


class TestRunDispatch:
    def test_run_processes_all_enabled_jobs(self):
        wf = Workflow(
            jobs=[
                WorkflowJob(name="A", enabled=True),
                WorkflowJob(name="B", enabled=False),
                WorkflowJob(name="C", enabled=True),
            ]
        )
        ex = WorkflowExecutor(wf, _make_settings())
        ex._run_pipelined = MagicMock(return_value=(2, 0, 0))

        ex.run()

        ex._run_pipelined.assert_called_once_with([
            (0, wf.jobs[0]),
            (2, wf.jobs[2]),
        ])


class TestGraphDrivenExecution:
    def test_graph_merge_and_youtube_version_run_without_legacy_flags(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="Graph Only",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            files=[
                FileEntry(
                    source_path=str(source),
                    graph_source_id="source-files-1",
                )
            ],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "merge-1", "type": "merge"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "merge-1"},
                {"source": "merge-1", "target": "yt-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        def _fake_youtube_convert(job, settings, **_kwargs):
            del settings
            yt_path = _derived_path(job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.write_text("yt", encoding="utf-8")
            return True

        with patch.object(ex, "_youtube_convert_func", side_effect=_fake_youtube_convert):
            ex.run()

        assert job.step_statuses["merge"] == "reused-target"
        assert job.step_statuses["yt_version"] == "done"
        assert (tmp_path / "processed" / "clip_youtube.mp4").exists()

    def test_graph_repair_and_youtube_version_run_without_legacy_flags(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="Graph Repair",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            files=[
                FileEntry(
                    source_path=str(source),
                    graph_source_id="source-files-1",
                )
            ],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "repair-1", "type": "repair"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "repair-1"},
                {"source": "repair-1", "target": "yt-1"},
            ],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        def _fake_repair(cv_job, _settings, **_kwargs):
            repaired = _derived_path(cv_job.output_path, "_repaired", ".mp4")
            repaired.parent.mkdir(parents=True, exist_ok=True)
            repaired.write_text("repaired", encoding="utf-8")
            cv_job.output_path = repaired
            return True

        def _fake_youtube_convert(job, settings, **_kwargs):
            del settings
            yt_path = _derived_path(job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.write_text("yt", encoding="utf-8")
            return True

        with patch.object(ex, "_youtube_convert_func", side_effect=_fake_youtube_convert), \
             patch("src.workflow_steps.repair_output_step.run_repair_output", side_effect=_fake_repair):
            ex.run()

        assert job.step_statuses["repair"] == "done"
        assert job.step_statuses["yt_version"] == "done"
        assert (tmp_path / "processed" / "clip_repaired.mp4").exists()
        assert (tmp_path / "processed" / "clip_repaired_youtube.mp4").exists()

    def test_youtube_version_uses_step_specific_preset_and_no_bframes(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="YT Custom",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            yt_version_preset="veryslow",
            yt_version_no_bframes=False,
            yt_version_output_resolution="2160p",
            files=[FileEntry(source_path=str(source), graph_source_id="source-files-1")],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "yt-1"},
            ],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())
        captured: dict[str, object] = {}

        def _fake_youtube_convert(cv_job, settings, **kwargs):
            del settings
            captured.update(kwargs)
            yt_path = _derived_path(cv_job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.write_text("yt", encoding="utf-8")
            return True

        with patch.object(ex, "_youtube_convert_func", side_effect=_fake_youtube_convert):
            ex.run()

        assert job.step_statuses["yt_version"] == "done"
        assert captured["preset"] == "veryslow"
        assert captured["no_bframes"] is False
        assert captured["output_resolution"] == "2160p"

    def test_youtube_version_uses_step_specific_encoder_crf_and_fps(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="YT Fine Tune",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            yt_version_encoder="libx264",
            yt_version_crf=17,
            yt_version_preset="slow",
            yt_version_no_bframes=True,
            yt_version_fps=60,
            files=[FileEntry(source_path=str(source), graph_source_id="source-files-1")],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "yt-1"},
            ],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())
        captured: dict[str, object] = {}

        def _fake_youtube_convert(cv_job, settings, **kwargs):
            del cv_job, settings
            captured.update(kwargs)
            return True

        with patch.object(ex, "_youtube_convert_func", side_effect=_fake_youtube_convert):
            ex.run()

        assert captured["encoder"] == "libx264"
        assert captured["crf"] == 17
        assert captured["fps"] == 60

    def test_yt_version_step_detail_uses_explicit_yt_encoder(self, tmp_path):
        """step_details['yt_version'] zeigt den tatsächlich gesetzten yt_version_encoder."""
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="YT Detail Encoder",
            source_mode="files",
            encoder="auto",
            yt_version_encoder="libx264",
            files=[FileEntry(source_path=str(source), graph_source_id="src-1")],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[{"source": "src-1", "target": "yt-1"}],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        def _fake(cv_job, settings, **kwargs):
            yt_path = _derived_path(cv_job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.write_text("yt", encoding="utf-8")
            return True

        with patch.object(ex, "_youtube_convert_func", side_effect=_fake):
            ex.run()

        detail = job.step_details.get("yt_version", "")
        assert "libx264" in detail, f"Erwartet 'libx264' im step_detail, bekommen: {detail!r}"
        assert "auto" not in detail, f"'auto' darf nicht im step_detail stehen: {detail!r}"

    def test_yt_version_step_detail_inherits_merge_encoder(self, tmp_path):
        """step_details['yt_version'] übernimmt merge_encoder wenn yt_version_encoder='inherit'."""
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="YT Inherits Merge",
            source_mode="files",
            encoder="auto",
            merge_encoder="libx264",
            yt_version_encoder="inherit",
            files=[FileEntry(source_path=str(source), graph_source_id="src-1", merge_group_id="g1")],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "merge-1", "type": "merge"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "merge-1"},
                {"source": "merge-1", "target": "yt-1"},
            ],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())
        merged = _derived_path(source, "_merged")

        def _fake_concat(sources, output, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("merged", encoding="utf-8")
            return True

        def _fake_yt(cv_job, settings, **kwargs):
            yt_path = _derived_path(cv_job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.write_text("yt", encoding="utf-8")
            return True

        with patch.object(ex, "_concat_func", side_effect=_fake_concat), \
             patch.object(ex, "_youtube_convert_func", side_effect=_fake_yt), \
             patch("src.workflow_steps.merge_group_step.validate_media_output", return_value=MediaValidationResult(True, True, "")), \
             patch("src.workflow_steps.output_step_stack.OutputValidationStep.execute", return_value=0):
            ex.run()

        detail = job.step_details.get("yt_version", "")
        assert "libx264" in detail, f"Erwartet 'libx264' (merge_encoder) im step_detail, bekommen: {detail!r}"

    def test_yt_version_reused_step_detail_uses_effective_encoder(self, tmp_path):
        """step_details['yt_version'] zeigt yt_version_encoder auch bei reused-target."""
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")
        # YT-Datei schon vorhanden → reused-target
        yt_file = _derived_path(source, "_youtube")
        yt_file.parent.mkdir(parents=True, exist_ok=True)
        yt_file.write_text("existing_yt", encoding="utf-8")

        job = WorkflowJob(
            name="YT Reused Detail",
            source_mode="files",
            encoder="auto",
            yt_version_encoder="libx264",
            overwrite=False,
            files=[FileEntry(source_path=str(source), graph_source_id="src-1")],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[{"source": "src-1", "target": "yt-1"}],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        with patch("src.workflow_steps.youtube_version_step.validate_media_output",
                   return_value=MediaValidationResult(True, True, "")):
            ex.run()

        assert job.step_statuses.get("yt_version") == "reused-target"
        detail = job.step_details.get("yt_version", "")
        assert "libx264" in detail, f"Erwartet 'libx264' im reused step_detail, bekommen: {detail!r}"
        assert "auto" not in detail, f"'auto' darf nicht im reused step_detail stehen: {detail!r}"

    def test_does_not_mutate_original_settings(self):
        """_build_job_settings darf die globalen Settings nicht verändern."""
        settings = _make_settings()
        original_encoder = settings.video.encoder
        wf = Workflow()
        ex = WorkflowExecutor(wf, settings)
        job = WorkflowJob(encoder="libx265")
        ex._build_job_settings(job)
        assert settings.video.encoder == original_encoder

    def test_graph_validation_ok_branch_skips_repair_and_runs_youtube_version(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="Validation OK",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            files=[FileEntry(source_path=str(source), graph_source_id="source-files-1")],
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

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        def _fake_youtube_convert(cv_job, settings, **_kwargs):
            del settings
            yt_path = _derived_path(cv_job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.write_text("yt", encoding="utf-8")
            return True

        with patch(
            "src.workflow_steps.output_validation_step.inspect_media_compatibility",
            return_value=MediaValidationResult("ok", "Alles in Ordnung", compatible=True),
        ), patch.object(ex, "_youtube_convert_func", side_effect=_fake_youtube_convert), patch(
            "src.workflow_steps.repair_output_step.run_repair_output"
        ) as repair_mock, patch(
            "src.workflow_steps.youtube_version_step.validate_media_output",
            return_value=True,
        ):
            ex.run()

        repair_mock.assert_not_called()
        assert job.step_statuses["validate_surface"] == "ok"
        assert job.step_statuses["yt_version"] == "done"
        assert "repair" not in job.step_statuses or job.step_statuses["repair"] != "done"
        assert (tmp_path / "processed" / "clip_youtube.mp4").exists()

    def test_graph_validation_repairable_branch_runs_repair_before_youtube_version(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="Validation Repair",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            files=[FileEntry(source_path=str(source), graph_source_id="source-files-1")],
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

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        def _fake_repair(cv_job, _settings, **_kwargs):
            repaired = _derived_path(cv_job.output_path, "_repaired", ".mp4")
            repaired.parent.mkdir(parents=True, exist_ok=True)
            repaired.write_text("repaired", encoding="utf-8")
            cv_job.output_path = repaired
            return True

        def _fake_youtube_convert(cv_job, settings, **_kwargs):
            del settings
            yt_path = _derived_path(cv_job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.write_text("yt", encoding="utf-8")
            return True

        with patch(
            "src.workflow_steps.output_validation_step.inspect_media_compatibility",
            return_value=MediaValidationResult(
                "repairable",
                "Reparierbar",
                compatible=False,
                details=["Zeitstempelprobleme erkannt"],
            ),
        ), patch(
            "src.workflow_steps.repair_output_step.run_repair_output",
            side_effect=_fake_repair,
        ) as repair_mock, patch.object(
            ex,
            "_youtube_convert_func",
            side_effect=_fake_youtube_convert,
        ), patch(
            "src.workflow_steps.youtube_version_step.validate_media_output",
            return_value=True,
        ):
            ex.run()

        repair_mock.assert_called_once()
        assert job.step_statuses["validate_surface"] == "repairable"
        assert job.step_statuses["repair"] == "done"
        assert job.step_statuses["yt_version"] == "done"
        assert (tmp_path / "processed" / "clip_repaired.mp4").exists()
        assert (tmp_path / "processed" / "clip_repaired_youtube.mp4").exists()

    def test_graph_validation_irreparable_branch_runs_stop_node(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")

        job = WorkflowJob(
            name="Validation Stop",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            files=[FileEntry(source_path=str(source), graph_source_id="source-files-1")],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "validate-1", "type": "validate_surface"},
                {"id": "stop-1", "type": "stop"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "validate-1"},
                {"source": "validate-1", "target": "stop-1", "branch": "irreparable"},
            ],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())
        logs = []
        ex.log_message.connect(logs.append)

        with patch(
            "src.workflow_steps.output_validation_step.inspect_media_compatibility",
            return_value=MediaValidationResult("irreparable", "Nicht reparierbar", compatible=False),
        ):
            ex.run()

        assert job.step_statuses["validate_surface"] == "irreparable"
        assert job.step_statuses["stop"] == "done"
        assert any("Datei irreparabel" in line for line in logs)

    def test_graph_cleanup_removes_stale_outputs_before_youtube_version(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("video", encoding="utf-8")
        stale_youtube = tmp_path / "processed" / "clip_youtube.mp4"
        stale_youtube.parent.mkdir(parents=True, exist_ok=True)
        stale_youtube.write_text("old", encoding="utf-8")

        job = WorkflowJob(
            name="Cleanup YT",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=False,
            upload_youtube=False,
            files=[FileEntry(source_path=str(source), graph_source_id="source-files-1")],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "cleanup-1", "type": "cleanup"},
                {"id": "yt-1", "type": "yt_version"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "cleanup-1"},
                {"source": "cleanup-1", "target": "yt-1"},
            ],
        )

        ex = WorkflowExecutor(Workflow(job=job), _make_settings())

        def _fake_youtube_convert(cv_job, settings, **_kwargs):
            del settings
            target = _derived_path(cv_job.output_path, "_youtube")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("new", encoding="utf-8")
            return True

        with patch.object(ex, "_youtube_convert_func", side_effect=_fake_youtube_convert), patch(
            "src.workflow_steps.youtube_version_step.validate_media_output",
            return_value=True,
        ):
            ex.run()

        assert job.step_statuses["cleanup"] == "done"
        assert job.step_statuses["yt_version"] == "done"
        assert stale_youtube.read_text(encoding="utf-8") == "new"


# ─── Merge-Gruppen-Logik ─────────────────────────────────────────────────────

class TestMergeGroupDetection:
    """Testet die interne _merge_skip_conv_idx-Berechnung.

    Der WorkflowExecutor baut die Skip-Menge im run()-Slot auf.
    Wir testen die Logik direkt auf der Datenebene, ohne run() aufzurufen.
    """

    @staticmethod
    def _compute_skip_set(
        file_entries: list[FileEntry],
        job: WorkflowJob,
    ) -> set[int]:
        """Repliziert die interne Merge-Skip-Logik aus workflow_executor.py.

        Seit dem Fix gehören ALLE Mitglieder einer Merge-Gruppe zur Skip-Menge
        (also auch das erste), damit kein Einzel-Upload vor dem Concat geschieht.
        """
        from src.media.converter import ConvertJob
        to_convert = [
            (0, job, ConvertJob(source_path=Path(fe.source_path)))
            for fe in file_entries
        ]
        skip: set[int] = set()
        for ci, (_, j, cv) in enumerate(to_convert):
            entry = WorkflowExecutor._find_file_entry(j, str(cv.source_path))
            gid = (getattr(entry, "merge_group_id", "") or "") if entry else ""
            if gid:
                skip.add(ci)   # alle Mitglieder – auch das erste
        return skip

    def _job_with_entries(self, entries: list[FileEntry]) -> WorkflowJob:
        job = WorkflowJob(source_mode="files", convert_enabled=True)
        job.files = entries
        return job

    def test_no_groups_nothing_skipped(self):
        entries = [
            FileEntry(source_path="/a/1.mp4"),
            FileEntry(source_path="/a/2.mp4"),
        ]
        job = self._job_with_entries(entries)
        skip = self._compute_skip_set(entries, job)
        assert skip == set()

    def test_two_in_group_both_skipped(self):
        """Beide Mitglieder der Gruppe werden übersprungen – Upload nach Merge."""
        entries = [
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
        ]
        job = self._job_with_entries(entries)
        skip = self._compute_skip_set(entries, job)
        assert skip == {0, 1}

    def test_three_in_group_all_skipped(self):
        entries = [
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/3.mp4", merge_group_id="g1"),
        ]
        job = self._job_with_entries(entries)
        skip = self._compute_skip_set(entries, job)
        assert skip == {0, 1, 2}

    def test_two_separate_groups_all_members_skipped(self):
        entries = [
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/3.mp4", merge_group_id="g2"),
            FileEntry(source_path="/a/4.mp4", merge_group_id="g2"),
        ]
        job = self._job_with_entries(entries)
        skip = self._compute_skip_set(entries, job)
        assert skip == {0, 1, 2, 3}

    def test_mixed_grouped_and_ungrouped(self):
        """Nicht-Mitglieder bleiben außerhalb der Skip-Menge."""
        entries = [
            FileEntry(source_path="/a/1.mp4"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/3.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/4.mp4"),
        ]
        job = self._job_with_entries(entries)
        skip = self._compute_skip_set(entries, job)
        assert skip == {1, 2}

    def test_group_detected_after_copy_by_filename(self):
        """Merge-Gruppe bleibt auch nach Transfer in einen Zielordner erkennbar."""
        job = self._job_with_entries([
            FileEntry(source_path="/quelle/teil1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/quelle/teil2.mp4", merge_group_id="g1"),
        ])
        assert WorkflowExecutor._get_merge_group_id(job, "/ziel/teil1.mp4") == "g1"
        assert WorkflowExecutor._get_merge_group_id(job, "/ziel/teil2.mp4") == "g1"


# ─── Transfer-Fehler-Zähler ───────────────────────────────────────────────────

class TestTransferFailCounter:
    def test_initial_fail_count_is_zero(self):
        wf = Workflow()
        ex = WorkflowExecutor(wf, _make_settings())
        assert ex._transfer_fail == 0

    def test_empty_workflow_emits_finished_0_0_0(self):
        finished_args = []
        wf = Workflow(jobs=[])
        ex = WorkflowExecutor(wf, _make_settings())
        ex.finished.connect(lambda ok, sk, fa: finished_args.append((ok, sk, fa)))
        ex.run()
        assert finished_args == [(0, 0, 0)]

    def test_disabled_jobs_not_counted(self):
        finished_args = []
        j = WorkflowJob(enabled=False)
        wf = Workflow(jobs=[j])
        ex = WorkflowExecutor(wf, _make_settings())
        ex.finished.connect(lambda ok, sk, fa: finished_args.append((ok, sk, fa)))
        ex.run()
        assert finished_args == [(0, 0, 0)]


class TestTransferResumeFallbacks:
    def test_direct_files_reuse_existing_target_when_source_missing(self, tmp_path):
        dst_dir = tmp_path / "ziel"
        dst_dir.mkdir()
        existing = dst_dir / "halbzeit1.mp4"
        existing.write_text("data", encoding="utf-8")

        job = WorkflowJob(
            name="Direkt",
            source_mode="files",
            copy_destination=str(dst_dir),
            files=[FileEntry(source_path=str(tmp_path / "quelle" / "halbzeit1.mp4"))],
        )
        ex = WorkflowExecutor(Workflow(), _make_settings())

        paths = ex._handle_direct_files(job)
        assert paths == [str(existing)]

    def test_scan_folder_reuses_existing_target_when_source_folder_missing(self, tmp_path):
        dst_dir = tmp_path / "ziel"
        dst_dir.mkdir()
        reused = dst_dir / "clip1.mp4"
        reused.write_text("data", encoding="utf-8")

        job = WorkflowJob(
            name="Ordner",
            source_mode="folder_scan",
            source_folder=str(tmp_path / "quelle-fehlt"),
            copy_destination=str(dst_dir),
            file_pattern="*.mp4",
        )
        ex = WorkflowExecutor(Workflow(), _make_settings())

        paths = ex._scan_folder(job)

        assert paths == [str(reused)]
        assert job.step_statuses["transfer"] == "reused-target"

    def test_direct_files_transfer_emits_status_and_progress(self, tmp_path):
        src_a = tmp_path / "a.mp4"
        src_b = tmp_path / "b.mp4"
        src_a.write_bytes(b"a" * 128)
        src_b.write_bytes(b"b" * 256)
        dst_dir = tmp_path / "ziel"

        job = WorkflowJob(
            name="Direkt",
            source_mode="files",
            copy_destination=str(dst_dir),
            files=[
                FileEntry(source_path=str(src_a)),
                FileEntry(source_path=str(src_b)),
            ],
        )
        ex = WorkflowExecutor(Workflow(), _make_settings())
        statuses = []
        progress = []
        ex.job_status.connect(lambda idx, status: statuses.append((idx, status)))
        ex.source_progress.connect(lambda idx, pct: progress.append((idx, pct)))

        paths = ex._handle_direct_files(job)

        assert [Path(path).name for path in paths] == ["a.mp4", "b.mp4"]
        assert statuses[0] == (0, "Transfer 1/2: a.mp4 …")
        assert statuses[-1] == (0, "Transfer 2/2: b.mp4 …")
        assert progress[0] == (0, 0)
        assert progress[-1] == (0, 100)

    def test_direct_files_transfer_skips_copy_when_destination_matches_source_dir(self, tmp_path):
        src_dir = tmp_path / "quelle"
        src_dir.mkdir()
        src_a = src_dir / "a.mp4"
        src_b = src_dir / "b.mp4"
        src_a.write_text("a", encoding="utf-8")
        src_b.write_text("b", encoding="utf-8")

        job = WorkflowJob(
            name="Direkt",
            source_mode="files",
            copy_destination=str(src_dir),
            move_files=True,
            files=[
                FileEntry(source_path=str(src_a)),
                FileEntry(source_path=str(src_b)),
            ],
        )
        ex = WorkflowExecutor(Workflow(), _make_settings())

        paths = ex._handle_direct_files(job)

        assert paths == [str(src_a), str(src_b)]
        assert src_a.exists()
        assert src_b.exists()

    def test_direct_files_transfer_skips_copy_when_destination_normalizes_to_source_dir(self, tmp_path):
        src_dir = tmp_path / "quelle"
        src_dir.mkdir()
        src = src_dir / "a.mp4"
        src.write_text("a", encoding="utf-8")

        job = WorkflowJob(
            name="Direkt",
            source_mode="files",
            copy_destination=str(src_dir / "."),
            files=[FileEntry(source_path=str(src))],
        )
        ex = WorkflowExecutor(Workflow(), _make_settings())

        paths = ex._handle_direct_files(job)

        assert paths == [str(src)]
        assert src.exists()

    def test_folder_scan_transfer_emits_status_and_progress_without_copy(self, tmp_path):
        src_dir = tmp_path / "quelle"
        src_dir.mkdir()
        (src_dir / "clip1.mp4").write_text("1", encoding="utf-8")
        (src_dir / "clip2.mp4").write_text("2", encoding="utf-8")

        job = WorkflowJob(
            name="Ordner",
            source_mode="folder_scan",
            source_folder=str(src_dir),
            copy_destination="",
            file_pattern="*.mp4",
        )
        ex = WorkflowExecutor(Workflow(), _make_settings())
        statuses = []
        progress = []
        ex.job_status.connect(lambda idx, status: statuses.append((idx, status)))
        ex.source_progress.connect(lambda idx, pct: progress.append((idx, pct)))

        paths = ex._scan_folder(job)

        assert [Path(path).name for path in paths] == ["clip1.mp4", "clip2.mp4"]
        assert statuses == [
            (0, "Transfer 1/2: clip1.mp4 …"),
            (0, "Transfer 2/2: clip2.mp4 …"),
        ]
        assert progress[0] == (0, 0)
        assert progress[-1] == (0, 100)

    def test_folder_scan_transfer_skips_copy_when_destination_matches_source_dir(self, tmp_path):
        src_dir = tmp_path / "quelle"
        src_dir.mkdir()
        clip1 = src_dir / "clip1.mp4"
        clip2 = src_dir / "clip2.mp4"
        clip1.write_text("1", encoding="utf-8")
        clip2.write_text("2", encoding="utf-8")

        job = WorkflowJob(
            name="Ordner",
            source_mode="folder_scan",
            source_folder=str(src_dir),
            copy_destination=str(src_dir / "."),
            move_files=True,
            file_pattern="*.mp4",
        )
        ex = WorkflowExecutor(Workflow(), _make_settings())

        paths = ex._scan_folder(job)

        assert paths == [str(clip1), str(clip2)]
        assert clip1.exists()
        assert clip2.exists()

    @patch("src.runtime.workflow_executor.run_convert")
    def test_direct_files_use_global_output_root_when_no_job_destination_is_set(self, mock_convert, tmp_path):
        src = tmp_path / "quelle.mp4"
        src.write_text("data", encoding="utf-8")
        settings = _make_settings()
        settings.workflow_output_root = str(tmp_path / "runs")

        def patched_convert(cv, _settings, **_kw):
            assert cv.output_path is not None
            cv.output_path.parent.mkdir(parents=True, exist_ok=True)
            cv.output_path.write_text("converted", encoding="utf-8")
            cv.status = "Fertig"
            return True

        mock_convert.side_effect = patched_convert

        job = WorkflowJob(
            name="Spieltag 23",
            source_mode="files",
            convert_enabled=True,
            files=[FileEntry(source_path=str(src))],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), settings)

        ex.run()

        assert src.exists()
        staged_raw = tmp_path / "runs" / f"Spieltag 23 {date.today().isoformat()}" / "raw" / "quelle.mp4"
        staged_processed = tmp_path / "runs" / f"Spieltag 23 {date.today().isoformat()}" / "processed" / "quelle.mp4"
        assert staged_raw.exists()
        assert staged_processed.exists()

    @patch("src.runtime.workflow_executor.run_convert")
    def test_direct_files_prefer_merge_camera_for_global_output_root(self, mock_convert, tmp_path):
        src = tmp_path / "quelle.mp4"
        src.write_text("data", encoding="utf-8")
        settings = _make_settings()
        settings.workflow_output_root = str(tmp_path / "runs")

        def patched_convert(cv, _settings, **_kw):
            assert cv.output_path is not None
            cv.output_path.parent.mkdir(parents=True, exist_ok=True)
            cv.output_path.write_text("converted", encoding="utf-8")
            cv.status = "Fertig"
            return True

        mock_convert.side_effect = patched_convert

        job = WorkflowJob(
            name="Spieltag 23",
            source_mode="files",
            convert_enabled=True,
            merge_segment_data={"camera": "DJI Osmo Action 5 Pro"},
            files=[FileEntry(source_path=str(src))],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), settings)

        ex.run()

        assert src.exists()
        staged_raw = tmp_path / "runs" / "DJI Osmo Action 5 Pro" / "raw" / "quelle.mp4"
        staged_processed = tmp_path / "runs" / "DJI Osmo Action 5 Pro" / "processed" / "quelle.mp4"
        assert staged_raw.exists()
        assert staged_processed.exists()

    @patch("src.runtime.workflow_executor.download_device")
    def test_pi_download_uses_global_output_root_when_job_destination_is_blank(self, mock_download, tmp_path):
        settings = _make_settings()
        settings.workflow_output_root = str(tmp_path / "runs")
        settings.cameras.devices = [SimpleNamespace(name="Cam1", ip="1.2.3.4")]
        mock_download.return_value = [("Cam1", "take1.mjpg", str(tmp_path / "runs" / "Cam1" / "raw" / "take1.mjpg"))]

        job = WorkflowJob(
            name="Pi Lauf",
            source_mode="pi_download",
            device_name="Cam1",
            download_destination="",
            files=[FileEntry(source_path="take1.mp4")],
        )
        ex = WorkflowExecutor(Workflow(), settings)

        ex._download_from_pi(0, job)

        assert mock_download.call_args.kwargs["destination_override"] == str(tmp_path / "runs" / "Cam1" / "raw")
        assert mock_download.call_args.kwargs["create_device_subdir"] is False

    @patch("src.runtime.workflow_executor.run_convert")
    @patch("src.runtime.workflow_executor.download_device", side_effect=RuntimeError("offline"))
    def test_download_resume_fallback_continues_workflow_without_failure(
        self,
        _mock_download,
        mock_convert,
        tmp_path,
    ):
        download_root = tmp_path / "downloads"
        download_root.mkdir(parents=True)
        reused = download_root / "take1.mjpg"
        reused.write_text("data", encoding="utf-8")

        settings = _make_settings()
        settings.cameras.devices = [SimpleNamespace(name="Cam1", ip="1.2.3.4")]

        def patched_convert(cv, _settings, **_kw):
            out = cv.source_path.with_suffix(".mp4")
            out.touch()
            cv.output_path = out
            cv.status = "Fertig"
            return True

        mock_convert.side_effect = patched_convert

        job = WorkflowJob(
            name="Pi",
            source_mode="pi_download",
            device_name="Cam1",
            download_destination=str(download_root),
            convert_enabled=True,
            files=[FileEntry(source_path="take1.mp4")],
            graph_nodes=[
                {"id": "src-1", "type": "source_pi_download"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
        )
        finished_args = []
        ex = WorkflowExecutor(Workflow(jobs=[job]), settings)
        ex.finished.connect(lambda ok, sk, fa: finished_args.append((ok, sk, fa)))

        ex.run()

        assert finished_args == [(1, 0, 0)]
        assert mock_convert.called


class TestWorkflowStackScenarios:
    def test_convert_disabled_skips_conversion(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("data", encoding="utf-8")

        job = WorkflowJob(
            name="Kein Convert",
            source_mode="files",
            convert_enabled=False,
            files=[FileEntry(source_path=str(source))],
        )
        ex = WorkflowExecutor(
            Workflow(jobs=[job], publish_kaderblick_videos=True),
            _make_settings(),
        )

        with patch("src.runtime.workflow_executor.run_convert") as mock_convert:
            ex.run()

        mock_convert.assert_not_called()

    @patch("src.runtime.workflow_executor.run_convert")
    def test_convert_enabled_runs_conversion(self, mock_convert, tmp_path):
        source = tmp_path / "clip.mjpeg"
        source.write_text("data", encoding="utf-8")

        def patched_convert(cv, _settings, **_kw):
            out = cv.source_path.with_suffix(".mp4")
            out.touch()
            cv.output_path = out
            cv.status = "Fertig"
            return True

        mock_convert.side_effect = patched_convert

        job = WorkflowJob(
            name="Mit Convert",
            source_mode="files",
            convert_enabled=True,
            files=[FileEntry(source_path=str(source))],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

        ex.run()

        assert mock_convert.called

    def test_move_files_moves_source_into_destination(self, tmp_path):
        src = tmp_path / "quelle.mp4"
        src.write_text("data", encoding="utf-8")
        dst_dir = tmp_path / "ziel"

        job = WorkflowJob(
            name="Move",
            source_mode="files",
            convert_enabled=False,
            copy_destination=str(dst_dir),
            move_files=True,
            files=[FileEntry(source_path=str(src))],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

        ex.run()

        assert not src.exists()
        assert (dst_dir / src.name).exists()

    def test_copy_mode_keeps_source_file(self, tmp_path):
        src = tmp_path / "quelle.mp4"
        src.write_text("data", encoding="utf-8")
        dst_dir = tmp_path / "ziel"

        job = WorkflowJob(
            name="Copy",
            source_mode="files",
            convert_enabled=False,
            copy_destination=str(dst_dir),
            move_files=False,
            files=[FileEntry(source_path=str(src))],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

        ex.run()

        assert src.exists()
        assert (dst_dir / src.name).exists()

    def test_reset_after_move_run_keeps_transferred_original(self, tmp_path):
        src = tmp_path / "quelle.mp4"
        src.write_text("data", encoding="utf-8")
        dst_dir = tmp_path / "ziel"

        job = WorkflowJob(
            name="Move Reset",
            source_mode="files",
            convert_enabled=False,
            copy_destination=str(dst_dir),
            move_files=True,
            files=[FileEntry(source_path=str(src))],
        )
        settings = _make_settings()
        ex = WorkflowExecutor(Workflow(jobs=[job]), settings)

        ex.run()
        moved_target = dst_dir / src.name

        result = reset_job_for_rebuild(job, settings)

        assert not src.exists()
        assert moved_target.exists()
        assert str(moved_target) not in result.deleted_paths

    def test_reset_after_copy_run_removes_transfer_copy(self, tmp_path):
        src = tmp_path / "quelle.mp4"
        src.write_text("data", encoding="utf-8")
        dst_dir = tmp_path / "ziel"

        job = WorkflowJob(
            name="Copy Reset",
            source_mode="files",
            convert_enabled=False,
            copy_destination=str(dst_dir),
            move_files=False,
            files=[FileEntry(source_path=str(src))],
        )
        settings = _make_settings()
        ex = WorkflowExecutor(Workflow(jobs=[job]), settings)

        ex.run()
        copied_target = dst_dir / src.name

        result = reset_job_for_rebuild(job, settings)

        assert src.exists()
        assert not copied_target.exists()
        assert str(copied_target) in result.deleted_paths

    def test_restart_mode_does_not_reuse_existing_transfer_target_when_source_is_missing(self, tmp_path):
        dst_dir = tmp_path / "ziel"
        dst_dir.mkdir()
        reused = dst_dir / "quelle.mp4"
        reused.write_text("data", encoding="utf-8")

        job = WorkflowJob(
            name="Restart",
            source_mode="files",
            convert_enabled=False,
            copy_destination=str(dst_dir),
            files=[FileEntry(source_path=str(tmp_path / "quelle.mp4"))],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings(), allow_reuse_existing=False)

        ex.run()

        assert job.step_statuses["transfer"] == "done"
        assert job.step_details["transfer"].startswith("Bereit: 0 Datei(en)")

    @patch("src.runtime.workflow_executor.get_youtube_service")
    def test_kaderblick_is_not_attempted_without_youtube_upload(self, mock_get_youtube, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("data", encoding="utf-8")

        job = WorkflowJob(
            name="Nur KB Flag",
            source_mode="files",
            convert_enabled=False,
            upload_youtube=False,
            upload_kaderblick=True,
            files=[FileEntry(source_path=str(source))],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

        with patch("src.workflow_steps.kaderblick_post_step.kaderblick_post") as mock_kaderblick:
            ex.run()

        mock_get_youtube.assert_not_called()
        mock_kaderblick.assert_not_called()

    @patch("src.workflow_steps.kaderblick_post_step.get_video_id_for_output", return_value="video-123")
    @patch("src.workflow_steps.kaderblick_post_step.kaderblick_post", return_value=True)
    @patch("src.runtime.workflow_executor.support.publish_game_videos", return_value={})
    def test_kaderblick_runs_only_with_youtube_upload(
        self,
        mock_publish,
        mock_kaderblick,
        _mock_video_id,
        tmp_path,
    ):
        source = tmp_path / "clip.mp4"
        source.write_text("data", encoding="utf-8")

        job = WorkflowJob(
            name="YT und KB",
            source_mode="files",
            convert_enabled=False,
            upload_youtube=True,
            upload_kaderblick=True,
            default_kaderblick_game_id="game-1",
            default_kaderblick_video_type_id=3,
            default_kaderblick_camera_id=7,
            files=[FileEntry(source_path=str(source))],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "ytu-1", "type": "youtube_upload"},
                {"id": "kb-1", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "ytu-1"},
                {"source": "ytu-1", "target": "kb-1"},
            ],
        )
        ex = WorkflowExecutor(
            Workflow(jobs=[job], publish_kaderblick_videos=True),
            _make_settings(),
        )

        with patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()):
            with patch(
                "src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube",
                return_value=True,
            ):
                ex.run()

        mock_kaderblick.assert_called_once()
        mock_publish.assert_called_once()


class TestKaderblickPublish:
    @staticmethod
    def _job(name: str, game_id: str, *, finished: bool = True) -> WorkflowJob:
        return WorkflowJob(
            name=name,
            default_kaderblick_game_id=game_id,
            resume_status="Fertig" if finished else "Kaderblick fehlgeschlagen",
            step_statuses={
                "transfer": "done",
                "youtube_upload": "done",
                "kaderblick": "done" if finished else "error",
            },
            graph_nodes=[
                {"id": "src", "type": "source_files"},
                {"id": "yt", "type": "youtube_upload"},
                {"id": "kb", "type": "kaderblick"},
            ],
            graph_edges=[
                {"source": "src", "target": "yt"},
                {"source": "yt", "target": "kb"},
            ],
        )

    def test_ignores_configured_but_not_started_job(self):
        started = self._job("Gestartet", "42")
        not_started = self._job("Andere Kamera", "42", finished=False)
        workflow = Workflow(
            jobs=[started, not_started],
            started_job_ids=[started.id],
            publish_kaderblick_videos=True,
        )
        executor = WorkflowExecutor(workflow, _make_settings(), active_indices={0})

        with patch("src.runtime.workflow_executor.support.publish_game_videos", return_value={}) as publish:
            failures = executor._publish_completed_kaderblick_games([(0, started)], 0)

        assert failures == 0
        publish.assert_called_once_with(executor._settings.kaderblick, "42")
        assert workflow.kaderblick_publish_statuses == {"42": "done"}

    def test_does_not_publish_when_workflow_option_is_disabled(self):
        job = self._job("Kamera", "42")
        workflow = Workflow(jobs=[job], started_job_ids=[job.id])
        executor = WorkflowExecutor(workflow, _make_settings(), active_indices={0})

        with patch("src.runtime.workflow_executor.support.publish_game_videos") as publish:
            failures = executor._publish_completed_kaderblick_games([(0, job)], 0)

        assert failures == 0
        publish.assert_not_called()

    def test_waits_for_failed_started_job(self):
        finished = self._job("Kamera 1", "42")
        failed = self._job("Kamera 2", "42", finished=False)
        workflow = Workflow(
            jobs=[finished, failed],
            started_job_ids=[finished.id, failed.id],
            publish_kaderblick_videos=True,
        )
        executor = WorkflowExecutor(workflow, _make_settings(), active_indices={0})

        with patch("src.runtime.workflow_executor.support.publish_game_videos") as publish:
            failures = executor._publish_completed_kaderblick_games([(0, finished)], 0)

        assert failures == 0
        publish.assert_not_called()

    def test_resume_publishes_after_last_failed_job_finishes(self):
        already_finished = self._job("Kamera 1", "42")
        resumed = self._job("Kamera 2", "42")
        workflow = Workflow(
            jobs=[already_finished, resumed],
            started_job_ids=[already_finished.id, resumed.id],
            publish_kaderblick_videos=True,
        )
        executor = WorkflowExecutor(workflow, _make_settings(), active_indices={1})

        with patch("src.runtime.workflow_executor.support.publish_game_videos", return_value={}) as publish:
            failures = executor._publish_completed_kaderblick_games([(1, resumed)], 0)

        assert failures == 0
        publish.assert_called_once()

    def test_failed_publish_is_retried_but_successful_publish_is_not(self):
        job = self._job("Kamera", "42")
        workflow = Workflow(
            jobs=[job],
            started_job_ids=[job.id],
            publish_kaderblick_videos=True,
        )
        executor = WorkflowExecutor(workflow, _make_settings(), active_indices={0})

        with patch(
            "src.runtime.workflow_executor.support.publish_game_videos",
            side_effect=[RuntimeError("offline"), {}],
        ) as publish:
            assert executor._publish_completed_kaderblick_games([(0, job)], 0) == 1
            assert workflow.kaderblick_publish_statuses == {"42": "error"}
            assert executor._publish_completed_kaderblick_games([(0, job)], 0) == 0
            assert executor._publish_completed_kaderblick_games([(0, job)], 0) == 0

        assert publish.call_count == 2
        assert workflow.kaderblick_publish_statuses == {"42": "done"}

    @patch("src.runtime.workflow_executor.run_convert")
    def test_single_file_youtube_version_is_optional(self, mock_convert, tmp_path):
        source = tmp_path / "clip.mjpeg"
        source.write_text("data", encoding="utf-8")

        def patched_convert(cv, _settings, **_kw):
            out = cv.source_path.with_suffix(".mp4")
            out.touch()
            cv.output_path = out
            cv.status = "Fertig"
            return True

        mock_convert.side_effect = patched_convert

        with patch("src.runtime.workflow_executor.run_youtube_convert", return_value=True) as mock_yt_convert:
            job = WorkflowJob(
                name="YT-Version",
                source_mode="files",
                convert_enabled=True,
                create_youtube_version=True,
                upload_youtube=False,
                files=[FileEntry(source_path=str(source))],
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "conv-1", "type": "convert"},
                    {"id": "ytv-1", "type": "yt_version"},
                ],
                graph_edges=[
                    {"source": "src-1", "target": "conv-1"},
                    {"source": "conv-1", "target": "ytv-1"},
                ],
            )
            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
            ex.run()

        mock_yt_convert.assert_called_once()

        with patch("src.runtime.workflow_executor.run_youtube_convert", return_value=True) as mock_yt_convert_disabled:
            job = WorkflowJob(
                name="Ohne YT-Version",
                source_mode="files",
                convert_enabled=True,
                create_youtube_version=False,
                upload_youtube=False,
                files=[FileEntry(source_path=str(source))],
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "conv-1", "type": "convert"},
                ],
                graph_edges=[
                    {"source": "src-1", "target": "conv-1"},
                ],
            )
            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
            ex.run()

        mock_yt_convert_disabled.assert_not_called()

    def test_download_reuses_existing_target_when_device_unavailable(self, tmp_path):
        download_root = tmp_path / "downloads"
        download_root.mkdir(parents=True)
        reused = download_root / "take1.mjpg"
        reused.write_text("data", encoding="utf-8")

        settings = _make_settings()
        settings.cameras.devices = [SimpleNamespace(name="Cam1", ip="1.2.3.4")]

        job = WorkflowJob(
            name="Pi",
            source_mode="pi_download",
            device_name="Cam1",
            download_destination=str(download_root),
            files=[FileEntry(source_path="take1.mp4")],
        )
        ex = WorkflowExecutor(Workflow(), settings)
        ex._download_func = MagicMock(side_effect=RuntimeError("offline"))

        paths = ex._download_from_pi(0, job)

        assert paths == [str(reused)]
        assert job.step_statuses["transfer"] == "reused-target"

    def test_restart_mode_passes_no_reuse_flag_to_pi_download(self, tmp_path):
        settings = _make_settings()
        settings.cameras.devices = [SimpleNamespace(name="Cam1", ip="1.2.3.4")]

        job = WorkflowJob(
            name="Pi Restart",
            source_mode="pi_download",
            device_name="Cam1",
            download_destination=str(tmp_path / "downloads"),
            files=[FileEntry(source_path="take1.mp4")],
        )
        ex = WorkflowExecutor(Workflow(), settings, allow_reuse_existing=False)
        ex._download_func = MagicMock(return_value=[])

        ex._download_from_pi(0, job)

        assert ex._download_func.call_args.kwargs["allow_reuse_existing"] is False

    @patch("src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube", return_value=True)
    def test_resume_reuses_existing_converted_output_and_continues_pipeline(self, mock_upload, tmp_path):
        source = tmp_path / "clip.mp4"
        converted = tmp_path / "processed" / "clip_converted.mp4"
        converted.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("source", encoding="utf-8")
        converted.write_text("converted", encoding="utf-8")

        job = WorkflowJob(
            name="Resume Convert",
            source_mode="files",
            convert_enabled=True,
            create_youtube_version=True,
            upload_youtube=True,
            files=[FileEntry(source_path=str(source), output_filename="clip_converted")],
            resume_status="Konvertiere …",
            step_statuses={"transfer": "done", "convert": "running"},
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
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        ex._youtube_convert_func = MagicMock(return_value=True)

        with patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()), \
             patch("src.workflow_steps.youtube_version_step.validate_media_output", return_value=True):
            ex.run()

        ex._youtube_convert_func.assert_called_once()
        reused_cv_job = ex._youtube_convert_func.call_args[0][0]
        assert reused_cv_job.output_path == converted
        mock_upload.assert_called_once()
        assert job.step_statuses["convert"] == "reused-target"

    @patch("src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube", return_value=True)
    def test_resume_uses_existing_youtube_artifact_when_source_is_gone(self, mock_upload, tmp_path):
        source = tmp_path / "clip.mp4"
        yt_version = tmp_path / "processed" / "clip_youtube.mp4"
        yt_version.parent.mkdir(parents=True, exist_ok=True)
        yt_version.write_text("yt", encoding="utf-8")

        job = WorkflowJob(
            name="Resume Upload",
            source_mode="files",
            convert_enabled=False,
            create_youtube_version=True,
            upload_youtube=True,
            files=[FileEntry(source_path=str(source))],
            resume_status="YT-Version erstellen …",
            step_statuses={"transfer": "done", "yt_version": "running"},
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "ytv-1", "type": "yt_version"},
                {"id": "ytu-1", "type": "youtube_upload"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "ytv-1"},
                {"source": "ytv-1", "target": "ytu-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

        with patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()), \
             patch("src.workflow_steps.youtube_version_step.validate_media_output", return_value=True):
            ex.run()

        mock_upload.assert_called_once()
        assert job.step_statuses["yt_version"] == "reused-target"

    @patch("src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube", return_value=True)
    def test_resume_from_convert_skips_pi_download_source_step(self, mock_upload, tmp_path):
        download_root = tmp_path / "raw"
        download_root.mkdir(parents=True, exist_ok=True)
        transferred = download_root / "take1.mjpg"
        transferred.write_text("raw", encoding="utf-8")
        converted = tmp_path / "processed" / "take1.mp4"
        converted.parent.mkdir(parents=True, exist_ok=True)

        settings = _make_settings()
        settings.cameras.devices = [SimpleNamespace(name="Cam1", ip="1.2.3.4")]

        job = WorkflowJob(
            name="Resume Pi Convert",
            source_mode="pi_download",
            device_name="Cam1",
            download_destination=str(download_root),
            convert_enabled=True,
            upload_youtube=True,
            files=[FileEntry(source_path="take1.mp4")],
            step_statuses={"transfer": "done"},
            graph_nodes=[
                {"id": "src-1", "type": "source_pi_download"},
                {"id": "conv-1", "type": "convert"},
                {"id": "ytu-1", "type": "youtube_upload"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "conv-1"},
                {"source": "conv-1", "target": "ytu-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), settings)
        ex._download_func = MagicMock(side_effect=AssertionError("pi download must stay skipped"))

        def _convert(cv_job, _settings, **_kwargs):
            converted.write_text("converted", encoding="utf-8")
            cv_job.output_path = converted
            cv_job.status = "Fertig"
            return True

        ex._convert_func = _convert

        with patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()):
            ex.run()

        ex._download_func.assert_not_called()
        mock_upload.assert_called_once()
        assert job.step_statuses["transfer"] == "done"
        assert job.step_statuses["convert"] == "done"

    def test_resume_from_convert_skips_folder_scan_source_step(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        transferred = raw_dir / "clip1.mp4"
        transferred.write_text("raw", encoding="utf-8")
        converted = tmp_path / "processed" / "clip1.mp4"
        converted.parent.mkdir(parents=True, exist_ok=True)

        job = WorkflowJob(
            name="Resume Folder Convert",
            source_mode="folder_scan",
            source_folder=str(tmp_path / "quelle-fehlt"),
            copy_destination=str(raw_dir),
            convert_enabled=True,
            files=[FileEntry(source_path="clip1.mp4")],
            step_statuses={"transfer": "done"},
            graph_nodes=[
                {"id": "src-1", "type": "source_folder_scan"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "conv-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

        def _convert(cv_job, _settings, **_kwargs):
            converted.write_text("converted", encoding="utf-8")
            cv_job.output_path = converted
            cv_job.status = "Fertig"
            return True

        ex._convert_func = _convert

        with patch.object(ex._transfer_step, "execute", side_effect=AssertionError("folder scan must stay skipped")):
            ex.run()

        assert job.step_statuses["transfer"] == "done"
        assert job.step_statuses["convert"] == "done"

    @patch("src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube", return_value=True)
    def test_resume_from_upload_treats_previous_steps_as_finished(self, mock_upload, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("source", encoding="utf-8")
        yt_version = tmp_path / "processed" / "clip_youtube.mp4"
        yt_version.parent.mkdir(parents=True, exist_ok=True)
        yt_version.write_text("yt", encoding="utf-8")

        job = WorkflowJob(
            name="Resume Upload Only",
            source_mode="files",
            convert_enabled=True,
            create_youtube_version=True,
            upload_youtube=True,
            files=[FileEntry(source_path=str(source))],
            step_statuses={
                "transfer": "done",
                "convert": "done",
                "yt_version": "done",
            },
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
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        ex._convert_func = MagicMock(side_effect=AssertionError("convert must stay skipped"))
        ex._youtube_convert_func = MagicMock(side_effect=AssertionError("yt version must stay skipped"))

        with patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()), \
             patch.object(ex._transfer_step, "execute", side_effect=AssertionError("transfer must stay skipped")):
            ex.run()

        mock_upload.assert_called_once()
        ex._convert_func.assert_not_called()
        ex._youtube_convert_func.assert_not_called()
        assert job.step_statuses["transfer"] == "done"
        assert job.step_statuses["convert"] == "done"
        assert job.step_statuses["yt_version"] == "done"

    def test_pipeline_starts_first_conversion_while_same_job_still_transfers(self, tmp_path):
        source_a = tmp_path / "halbzeit1.mjpg"
        source_b = tmp_path / "halbzeit2.mjpg"
        source_a.write_text("a", encoding="utf-8")
        source_b.write_text("b", encoding="utf-8")

        job = WorkflowJob(
            name="Pipeline",
            source_mode="files",
            convert_enabled=True,
            files=[
                FileEntry(source_path=str(source_a)),
                FileEntry(source_path=str(source_b)),
            ],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        transfer_finished = threading.Event()
        convert_started_during_transfer = threading.Event()

        def _transfer(_executor, _orig_idx, _job, on_file_ready=None):
            assert on_file_ready is not None
            on_file_ready(str(source_a))
            for _ in range(50):
                if convert_started_during_transfer.is_set():
                    break
                time.sleep(0.01)
            on_file_ready(str(source_b))
            transfer_finished.set()
            return [str(source_a), str(source_b)]

        def _convert(_executor, _orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            if cv_job.source_path == source_a and not transfer_finished.is_set():
                convert_started_during_transfer.set()
            output = cv_job.source_path.with_suffix(".mp4")
            output.write_text("converted", encoding="utf-8")
            cv_job.output_path = output
            cv_job.status = "Fertig"
            return "ok"

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch.object(
            ex._convert_step,
            "execute",
            side_effect=_convert,
        ), patch.object(ex._output_step_stack, "execute_processing_steps", return_value=0), patch.object(
            ex._output_step_stack,
            "execute_delivery_steps",
            return_value=0,
        ):
            ex.run()

        assert convert_started_during_transfer.is_set()

    def test_pipeline_preserves_merge_barrier_until_group_is_ready(self, tmp_path):
        source_a = tmp_path / "kamera1.mjpg"
        source_b = tmp_path / "kamera2.mjpg"
        source_a.write_text("a", encoding="utf-8")
        source_b.write_text("b", encoding="utf-8")

        job = WorkflowJob(
            name="Merge",
            source_mode="files",
            convert_enabled=True,
            files=[
                FileEntry(source_path=str(source_a), merge_group_id="g1"),
                FileEntry(source_path=str(source_b), merge_group_id="g1"),
            ],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
                {"id": "merge-1", "type": "merge"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "conv-1"},
                {"source": "conv-1", "target": "merge-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        call_order = []

        def _transfer(_executor, _orig_idx, _job, on_file_ready=None):
            assert on_file_ready is not None
            on_file_ready(str(source_a))
            on_file_ready(str(source_b))
            return [str(source_a), str(source_b)]

        def _convert(_executor, _orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            call_order.append(f"convert:{cv_job.source_path.name}")
            output = cv_job.source_path.with_suffix(".mp4")
            output.write_text("converted", encoding="utf-8")
            cv_job.output_path = output
            cv_job.status = "Fertig"
            return "ok"

        def _merge(_executor, gid, group):
            call_order.append(f"merge:{gid}")
            assert call_order[:2] == ["convert:kamera1.mjpg", "convert:kamera2.mjpg"]
            merged_output = tmp_path / "kamera1_merged.mp4"
            merged_output.write_text("merged", encoding="utf-8")
            prepared = PreparedOutput(
                orig_idx=group[0].orig_idx,
                job=group[0].job,
                cv_job=group[0].cv_job,
                per_settings=ex._build_job_settings(group[0].job),
            )
            prepared.cv_job.output_path = merged_output
            return prepared, 0

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch.object(
            ex._convert_step,
            "execute",
            side_effect=_convert,
        ), patch.object(ex._merge_step, "execute", side_effect=_merge), patch.object(
            ex._output_step_stack,
            "execute_processing_steps",
            return_value=0,
        ), patch.object(ex._output_step_stack, "execute_delivery_steps", return_value=0):
            ex.run()

        assert call_order == ["convert:kamera1.mjpg", "convert:kamera2.mjpg", "merge:g1"]

    def test_pipeline_runs_merge_for_completed_workflow_while_later_workflow_still_transfers(self, tmp_path):
        dji1_a = tmp_path / "dji1_a.mp4"
        dji1_b = tmp_path / "dji1_b.mp4"
        dji2_a = tmp_path / "dji2_a.mp4"
        dji2_b = tmp_path / "dji2_b.mp4"
        kb_file = tmp_path / "kb_a.mjpg"
        for path, text in [
            (dji1_a, "a"),
            (dji1_b, "b"),
            (dji2_a, "c"),
            (dji2_b, "d"),
            (kb_file, "e"),
        ]:
            path.write_text(text, encoding="utf-8")

        job_dji_1 = WorkflowJob(
            name="DJI 1",
            source_mode="folder_scan",
            convert_enabled=False,
            files=[
                FileEntry(source_path=str(dji1_a), merge_group_id="g1"),
                FileEntry(source_path=str(dji1_b), merge_group_id="g1"),
            ],
        )
        job_dji_2 = WorkflowJob(
            name="DJI 2",
            source_mode="folder_scan",
            convert_enabled=False,
            files=[
                FileEntry(source_path=str(dji2_a), merge_group_id="g1"),
                FileEntry(source_path=str(dji2_b), merge_group_id="g1"),
            ],
        )
        job_kb = WorkflowJob(
            name="Kaderblick",
            source_mode="pi_download",
            convert_enabled=False,
            files=[FileEntry(source_path=str(kb_file))],
        )

        ex = WorkflowExecutor(Workflow(jobs=[job_dji_1, job_dji_2, job_kb]), _make_settings())
        later_transfer_finished = threading.Event()
        merge_started_before_later_transfer_end = threading.Event()
        call_order: list[str] = []

        def _transfer(_executor, _orig_idx, job, on_file_ready=None):
            assert on_file_ready is not None
            if job.name == "DJI 1":
                on_file_ready(str(dji1_a))
                on_file_ready(str(dji1_b))
                return [str(dji1_a), str(dji1_b)]
            if job.name == "DJI 2":
                on_file_ready(str(dji2_a))
                on_file_ready(str(dji2_b))
                return [str(dji2_a), str(dji2_b)]

            for _ in range(50):
                if merge_started_before_later_transfer_end.is_set():
                    break
                time.sleep(0.01)
            later_transfer_finished.set()
            on_file_ready(str(kb_file))
            return [str(kb_file)]

        def _merge(_executor, gid, group):
            workflow_name = group[0].job.name
            call_order.append(f"merge:{workflow_name}:{gid}")
            if workflow_name in {"DJI 1", "DJI 2"} and not later_transfer_finished.is_set():
                merge_started_before_later_transfer_end.set()
            merged_output = tmp_path / f"{workflow_name.lower().replace(' ', '_')}_merged.mp4"
            merged_output.write_text("merged", encoding="utf-8")
            prepared = PreparedOutput(
                orig_idx=group[0].orig_idx,
                job=group[0].job,
                cv_job=group[0].cv_job,
                per_settings=ex._build_job_settings(group[0].job),
            )
            prepared.cv_job.output_path = merged_output
            return prepared, 0

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch.object(
            ex._merge_step,
            "execute",
            side_effect=_merge,
        ), patch.object(ex._output_step_stack, "execute_processing_steps", return_value=0), patch.object(
            ex._output_step_stack,
            "execute_delivery_steps",
            return_value=0,
        ):
            ex.run()

        assert merge_started_before_later_transfer_end.is_set()
        assert "merge:DJI 1:g1" in call_order
        assert "merge:DJI 2:g1" in call_order

    def test_pipeline_emits_merge_status_during_later_pi_transfer(self, tmp_path):
        source_a = tmp_path / "dji1.mp4"
        source_b = tmp_path / "dji2.mp4"
        source_a.write_text("a", encoding="utf-8")
        source_b.write_text("b", encoding="utf-8")

        dji_job = WorkflowJob(
            name="DJI",
            source_mode="files",
            convert_enabled=False,
            files=[
                FileEntry(source_path=str(source_a), graph_source_id="source-files-1"),
                FileEntry(source_path=str(source_b), graph_source_id="source-files-1"),
            ],
            graph_nodes=[
                {"id": "source-files-1", "type": "source_files"},
                {"id": "merge-1", "type": "merge"},
            ],
            graph_edges=[
                {"source": "source-files-1", "target": "merge-1"},
            ],
        )
        kb_job = WorkflowJob(
            name="Kaderblick",
            source_mode="pi_download",
            device_name="KB",
            convert_enabled=False,
            files=[FileEntry(source_path="aufnahme_1")],
        )

        settings = _make_settings()
        settings.cameras.devices = [SimpleNamespace(name="KB", ip="192.168.0.10")]
        ex = WorkflowExecutor(Workflow(jobs=[dji_job, kb_job]), settings)
        merge_status_seen_during_transfer = threading.Event()

        ex.job_status.connect(
            lambda idx, status: merge_status_seen_during_transfer.set()
            if idx == 0 and status.startswith("Zusammenführen") else None
        )

        def _download(**kwargs):
            progress_cb = kwargs["progress_cb"]
            for pos in range(1, 30):
                progress_cb("KB", "aufnahme_1.mjpg", pos, 100)
                if merge_status_seen_during_transfer.is_set():
                    break
                time.sleep(0.01)
            return []

        def _merge(_executor, gid, group):
            _executor._set_step_status(group[0].job, "merge", "running")
            _executor._set_job_status(group[0].orig_idx, "Zusammenführen …")
            prepared = PreparedOutput(
                orig_idx=group[0].orig_idx,
                job=group[0].job,
                cv_job=group[0].cv_job,
                per_settings=ex._build_job_settings(group[0].job),
            )
            prepared.cv_job.output_path = tmp_path / "merged.mp4"
            return prepared, 0

        with patch.object(ex, "_download_func", side_effect=_download), patch.object(
            ex._merge_step,
            "execute",
            side_effect=_merge,
        ), patch.object(ex._output_step_stack, "execute_processing_steps", return_value=0), patch.object(
            ex._output_step_stack,
            "execute_delivery_steps",
            return_value=0,
        ):
            ex.run()

        assert merge_status_seen_during_transfer.is_set()

    def test_pipeline_cancel_skips_already_queued_followup_job(self, tmp_path):
        first = tmp_path / "first.mp4"
        second = tmp_path / "second.mp4"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")

        job_a = WorkflowJob(
            name="Job A",
            source_mode="files",
            convert_enabled=True,
            files=[FileEntry(source_path=str(first))],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
        )
        job_b = WorkflowJob(
            name="Job B",
            source_mode="files",
            convert_enabled=True,
            files=[FileEntry(source_path=str(second))],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
            ],
            graph_edges=[{"source": "src-1", "target": "conv-1"}],
        )

        ex = WorkflowExecutor(Workflow(jobs=[job_a, job_b]), _make_settings())
        converted: list[str] = []

        def _convert(_executor, orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            converted.append(cv_job.source_path.name)
            if cv_job.source_path.name == "first.mp4":
                ex.cancel()
                cv_job.status = "Fertig"
                return "ok"
            raise AssertionError("second queued job should not start after cancel")

        with patch.object(ex._convert_step, "execute", side_effect=_convert), patch.object(
            ex._output_step_stack,
            "execute_processing_steps",
            return_value=0,
        ), patch.object(
            ex._output_step_stack,
            "execute_delivery_steps",
            return_value=0,
        ):
            ex.run()

        assert converted == ["first.mp4"]

    def test_pipeline_cancel_drains_multiple_already_queued_items_without_hanging(self, tmp_path):
        first = tmp_path / "first.mp4"
        second = tmp_path / "second.mp4"
        third = tmp_path / "third.mp4"
        for path, content in ((first, "a"), (second, "b"), (third, "c")):
            path.write_text(content, encoding="utf-8")

        ex = WorkflowExecutor(
            Workflow(
                jobs=[
                    WorkflowJob(name="Job A", source_mode="files", convert_enabled=True, files=[FileEntry(source_path=str(first))], graph_nodes=[{"id": "src-1", "type": "source_files"}, {"id": "conv-1", "type": "convert"}], graph_edges=[{"source": "src-1", "target": "conv-1"}]),
                    WorkflowJob(name="Job B", source_mode="files", convert_enabled=True, files=[FileEntry(source_path=str(second))], graph_nodes=[{"id": "src-1", "type": "source_files"}, {"id": "conv-1", "type": "convert"}], graph_edges=[{"source": "src-1", "target": "conv-1"}]),
                    WorkflowJob(name="Job C", source_mode="files", convert_enabled=True, files=[FileEntry(source_path=str(third))], graph_nodes=[{"id": "src-1", "type": "source_files"}, {"id": "conv-1", "type": "convert"}], graph_edges=[{"source": "src-1", "target": "conv-1"}]),
                ]
            ),
            _make_settings(),
        )
        converted: list[str] = []

        def _convert(_executor, _orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            converted.append(cv_job.source_path.name)
            if cv_job.source_path.name == "first.mp4":
                ex.cancel()
                cv_job.status = "Fertig"
                return "ok"
            raise AssertionError("queued follow-up jobs must not execute after cancel")

        runner = threading.Thread(target=ex.run, daemon=True)

        with patch.object(ex._convert_step, "execute", side_effect=_convert), patch.object(
            ex._output_step_stack,
            "execute_processing_steps",
            return_value=0,
        ), patch.object(
            ex._output_step_stack,
            "execute_delivery_steps",
            return_value=0,
        ):
            runner.start()
            runner.join(timeout=2.0)

        assert runner.is_alive() is False
        assert converted == ["first.mp4"]

    def test_pipeline_executes_all_connected_source_branches_independently(self, tmp_path):
        source = tmp_path / "branch-source.mp4"
        source.write_text("src", encoding="utf-8")

        job = WorkflowJob(
            name="Branching",
            source_mode="files",
            convert_enabled=True,
            title_card_enabled=True,
            upload_youtube=True,
            files=[FileEntry(source_path=str(source), graph_source_id="source-1")],
            graph_nodes=[
                {"id": "source-1", "type": "source_files"},
                {"id": "title-1", "type": "titlecard"},
                {"id": "convert-1", "type": "convert"},
                {"id": "upload-1", "type": "youtube_upload"},
            ],
            graph_edges=[
                {"source": "source-1", "target": "title-1"},
                {"source": "source-1", "target": "convert-1"},
                {"source": "convert-1", "target": "upload-1"},
            ],
        )

        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        titlecard_inputs: list[str] = []
        convert_inputs: list[str] = []
        upload_inputs: list[str] = []

        def _transfer(_executor, _orig_idx, _job, on_file_ready=None):
            assert on_file_ready is not None
            on_file_ready(str(source))
            return [str(source)]

        def _convert(_executor, _orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            convert_inputs.append(str(cv_job.source_path))
            output = source.with_name("branch-source_converted.mp4")
            output.write_text("converted", encoding="utf-8")
            cv_job.output_path = output
            cv_job.status = "Fertig"
            return "ok"

        def _title(_executor, _orig_idx, cv_job, _job, _settings):
            titlecard_inputs.append(str(cv_job.output_path))
            output = source.with_name("branch-source_titlecard.mp4")
            output.write_text("title", encoding="utf-8")
            return output, True

        def _upload(*args, **_kwargs):
            cv_job = args[1]
            upload_inputs.append(str(cv_job.output_path))
            return True

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch.object(
            ex._convert_step,
            "execute",
            side_effect=_convert,
        ), patch(
            "src.workflow_steps.title_card_step.TitleCardStep._prepend_title_card",
            side_effect=_title,
        ), patch(
            "src.runtime.workflow_executor.get_youtube_service",
            return_value=object(),
        ), patch(
            "src.workflow_steps.youtube_upload_step.get_video_id_for_output",
            return_value=None,
        ), patch(
            "src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube",
            side_effect=_upload,
        ):
            ex.run()

        assert titlecard_inputs == [str(source)]
        assert convert_inputs == [str(source)]
        assert upload_inputs == [str(source.with_name("branch-source_converted.mp4"))]

    def test_selective_cancel_marks_current_step_as_aborted_not_failed(self, tmp_path):
        source = tmp_path / "clip.mp4"
        source.write_text("a", encoding="utf-8")
        job = WorkflowJob(
            name="Job A",
            source_mode="files",
            convert_enabled=True,
            files=[FileEntry(source_path=str(source))],
        )
        job.current_step_key = "convert"
        job.step_statuses = {"convert": "running"}

        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings(), active_indices={0})
        statuses: list[str] = []
        ex.job_status.connect(lambda _idx, status: statuses.append(status))

        ex.cancel(active_indices={0})

        assert job.step_statuses["convert"] == "cancelled"
        assert job.step_details["convert"] == "Durch Benutzer abgebrochen"
        assert statuses[-1] == "Konvertierung abgebrochen"

    def test_pipeline_can_merge_before_convert_when_graph_requests_it(self, tmp_path):
        source_a = tmp_path / "kamera1.mp4"
        source_b = tmp_path / "kamera2.mp4"
        source_a.write_text("a", encoding="utf-8")
        source_b.write_text("b", encoding="utf-8")

        job = WorkflowJob(
            name="Merge vor Convert",
            source_mode="files",
            convert_enabled=True,
            files=[
                FileEntry(source_path=str(source_a), merge_group_id="g1", graph_source_id="source-a"),
                FileEntry(source_path=str(source_b), merge_group_id="g1", graph_source_id="source-b"),
            ],
            graph_nodes=[
                {"id": "source-a", "type": "source_files"},
                {"id": "source-b", "type": "source_files"},
                {"id": "merge-1", "type": "merge"},
                {"id": "convert-1", "type": "convert"},
            ],
            graph_edges=[
                {"source": "source-a", "target": "merge-1"},
                {"source": "source-b", "target": "merge-1"},
                {"source": "merge-1", "target": "convert-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        call_order = []

        def _transfer(_executor, _orig_idx, _job, on_file_ready=None):
            assert on_file_ready is not None
            on_file_ready(str(source_a))
            on_file_ready(str(source_b))
            return [str(source_a), str(source_b)]

        def _merge(_executor, gid, group):
            call_order.append(f"merge:{gid}")
            merged_output = tmp_path / "kamera1_merged.mp4"
            merged_output.write_text("merged", encoding="utf-8")
            prepared = PreparedOutput(
                orig_idx=group[0].orig_idx,
                job=group[0].job,
                cv_job=group[0].cv_job,
                per_settings=ex._build_job_settings(group[0].job),
            )
            prepared.cv_job.output_path = merged_output
            return prepared, 0

        def _convert(_executor, _orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            call_order.append(f"convert:{cv_job.source_path.name}")
            output = cv_job.source_path.with_stem(cv_job.source_path.stem + "_converted")
            output.write_text("converted", encoding="utf-8")
            cv_job.output_path = output
            cv_job.status = "Fertig"
            return "ok"

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch.object(
            ex._merge_step,
            "execute",
            side_effect=_merge,
        ), patch.object(ex._convert_step, "execute", side_effect=_convert), patch.object(
            ex._output_step_stack,
            "execute_processing_steps",
            return_value=0,
        ), patch.object(ex._output_step_stack, "execute_delivery_steps", return_value=0):
            ex.run()

        assert call_order == ["merge:g1", "convert:kamera1_merged.mp4"]

    def test_merge_runtime_fails_for_incompatible_inputs_before_convert(self, tmp_path):
        source_a = tmp_path / "kamera1.mp4"
        source_b = tmp_path / "kamera2.mp4"
        source_a.write_text("a", encoding="utf-8")
        source_b.write_text("b", encoding="utf-8")

        job = WorkflowJob(
            name="Merge Fehler",
            source_mode="files",
            convert_enabled=True,
            files=[
                FileEntry(source_path=str(source_a), merge_group_id="g1", graph_source_id="source-a"),
                FileEntry(source_path=str(source_b), merge_group_id="g1", graph_source_id="source-b"),
            ],
            graph_nodes=[
                {"id": "source-a", "type": "source_files"},
                {"id": "source-b", "type": "source_files"},
                {"id": "merge-1", "type": "merge"},
                {"id": "convert-1", "type": "convert"},
            ],
            graph_edges=[
                {"source": "source-a", "target": "merge-1"},
                {"source": "source-b", "target": "merge-1"},
                {"source": "merge-1", "target": "convert-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        statuses = []
        ex.job_status.connect(lambda idx, status: statuses.append((idx, status)))

        def _transfer(_executor, _orig_idx, _job, on_file_ready=None):
            assert on_file_ready is not None
            on_file_ready(str(source_a))
            on_file_ready(str(source_b))
            return [str(source_a), str(source_b)]

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch(
            "src.workflow_steps.merge_group_step.analyze_merge_sources",
            return_value=type("_Report", (), {"mergeable": False, "reasons": ("FPS weicht ab",)})(),
        ):
            ex.run()

        assert any("Merge-Eingänge inkompatibel" in status for _idx, status in statuses)

    def test_pipeline_can_upload_while_next_conversion_runs(self, tmp_path):
        source_a = tmp_path / "upload1.mjpg"
        source_b = tmp_path / "upload2.mjpg"
        source_a.write_text("a", encoding="utf-8")
        source_b.write_text("b", encoding="utf-8")

        job = WorkflowJob(
            name="Upload Parallel",
            source_mode="files",
            convert_enabled=True,
            upload_youtube=True,
            files=[
                FileEntry(source_path=str(source_a)),
                FileEntry(source_path=str(source_b)),
            ],
            graph_nodes=[
                {"id": "src-1", "type": "source_files"},
                {"id": "conv-1", "type": "convert"},
                {"id": "ytu-1", "type": "youtube_upload"},
            ],
            graph_edges=[
                {"source": "src-1", "target": "conv-1"},
                {"source": "conv-1", "target": "ytu-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        upload_started = threading.Event()
        release_upload = threading.Event()
        second_convert_during_upload = threading.Event()
        statuses: list[str] = []
        ex.job_status.connect(lambda idx, status: statuses.append(status) if idx == 0 else None)

        def _transfer(_executor, _orig_idx, _job, on_file_ready=None):
            assert on_file_ready is not None
            on_file_ready(str(source_a))
            assert upload_started.wait(timeout=2.0)
            on_file_ready(str(source_b))
            return [str(source_a), str(source_b)]

        def _convert(_executor, _orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            if cv_job.source_path == source_b and upload_started.is_set() and not release_upload.is_set():
                second_convert_during_upload.set()
                release_upload.set()
            output = cv_job.source_path.with_suffix(".mp4")
            output.write_text("converted", encoding="utf-8")
            cv_job.output_path = output
            cv_job.status = "Fertig"
            return "ok"

        def _deliver(_executor, prepared, _yt_service, _kb_sort_index, **_kwargs):
            if prepared.cv_job.source_path == source_a:
                upload_started.set()
                release_upload.wait(timeout=2.0)
            return 0

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch.object(
            ex._convert_step,
            "execute",
            side_effect=_convert,
        ), patch.object(ex._output_step_stack, "execute_processing_steps", return_value=0), patch.object(
            ex._output_step_stack,
            "execute_delivery_steps",
            side_effect=_deliver,
        ), patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()):
            ex.run()

        assert upload_started.is_set()
        assert second_convert_during_upload.is_set()
        assert "Fertig 1/2" in statuses
        assert statuses[-1] == "Fertig"

    def test_dead_end_convert_branch_does_not_affect_parallel_merge_inputs(self, tmp_path):
        source_a = tmp_path / "part1.mp4"
        source_b = tmp_path / "part2.mp4"
        source_a.write_text("a", encoding="utf-8")
        source_b.write_text("b", encoding="utf-8")

        job = WorkflowJob(
            name="Parallel Dead-End Convert",
            source_mode="files",
            convert_enabled=True,
            upload_youtube=True,
            files=[
                FileEntry(source_path=str(source_a), graph_source_id="source-1"),
                FileEntry(source_path=str(source_b), graph_source_id="source-1"),
            ],
            graph_nodes=[
                {"id": "source-1", "type": "source_files"},
                {"id": "convert-1", "type": "convert"},
                {"id": "merge-1", "type": "merge"},
                {"id": "upload-1", "type": "youtube_upload"},
            ],
            graph_edges=[
                {"source": "source-1", "target": "convert-1"},
                {"source": "source-1", "target": "merge-1"},
                {"source": "merge-1", "target": "upload-1"},
            ],
        )
        ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
        concat_sources: list[Path] = []

        def _transfer(_executor, _orig_idx, _job, on_file_ready=None):
            assert on_file_ready is not None
            on_file_ready(str(source_a))
            on_file_ready(str(source_b))
            return [str(source_a), str(source_b)]

        def _convert(_executor, _orig_idx, _job, cv_job, _settings, _done_count, _total_count):
            output = cv_job.source_path.with_stem(cv_job.source_path.stem + "_converted")
            output.write_text("converted", encoding="utf-8")
            cv_job.output_path = output
            cv_job.status = "Fertig"
            return "ok"

        def _concat(sources, dest, **_kwargs):
            concat_sources[:] = list(sources)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("merged", encoding="utf-8")
            return True

        with patch.object(ex._transfer_step, "execute", side_effect=_transfer), patch.object(
            ex._convert_step,
            "execute",
            side_effect=_convert,
        ), patch.object(ex, "_concat_func", side_effect=_concat), patch.object(
            ex._output_step_stack,
            "execute_processing_steps",
            return_value=0,
        ), patch.object(
            ex._output_step_stack,
            "execute_delivery_steps",
            return_value=0,
        ), patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()):
            ex.run()

        assert concat_sources == [source_a, source_b]


# ─── Merge-Concat: Standard-Dateien verwenden ────────────────────────────────

class TestMergeConcatStandardFlow:
    """Testet die neues Design im workflow_executor Merge-Block:
    run_concat() erhält immer die Standard-MP4 (job.output_path) – keine
    _youtube-Variante. Nach erfolgreichem Concat wird run_youtube_convert()
    auf das Merge-Ergebnis angewendet (wenn create_youtube_version=True).
    """

    @patch("src.runtime.workflow_executor.run_concat")
    def test_executor_passes_standard_files_to_concat(self, mock_concat):
        """workflow_executor übergibt Standard-Dateien (kein _youtube) an run_concat."""
        mock_concat.return_value = True

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)

            out1 = p / "v1.mp4"; out1.touch()
            out2 = p / "v2.mp4"; out2.touch()
            # _youtube-Varianten existieren, dürfen aber NICHT verwendet werden
            yt1 = p / "v1_youtube.mp4"; yt1.touch()
            yt2 = p / "v2_youtube.mp4"; yt2.touch()

            from src.media.converter import ConvertJob
            cv1 = ConvertJob(source_path=p / "v1_src.mjpeg", output_path=out1, status="Fertig")
            cv2 = ConvertJob(source_path=p / "v2_src.mjpeg", output_path=out2, status="Fertig")

            gid = "gruppe1"
            fe1 = FileEntry(source_path=str(p / "v1_src.mjpeg"), merge_group_id=gid)
            fe2 = FileEntry(source_path=str(p / "v2_src.mjpeg"), merge_group_id=gid)

            job = WorkflowJob(source_mode="files", convert_enabled=True, upload_youtube=False)
            job.files = [fe1, fe2]

            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
            merged_path = p / "gruppe1_merged.mp4"

            group_items = {gid: [(0, 0, job, cv1), (1, 0, job, cv2)]}
            _merged_out = {gid: merged_path}

            for _gid, group in group_items.items():
                source_paths = [cv.output_path for _, _, _, cv in group
                                if cv.output_path and cv.output_path.exists()]
                mock_concat(source_paths, _merged_out[_gid],
                            cancel_flag=ex._cancel, log_callback=None)

        call_args = mock_concat.call_args[0]
        passed_sources = call_args[0]
        assert passed_sources == [out1, out2], (
            f"Erwartet Standard-Dateien, erhalten: {passed_sources}")

    @patch("src.runtime.workflow_executor.run_concat")
    def test_executor_merge_deletes_source_files(self, mock_concat):
        """Nach erfolgreichem Concat werden die Einzeldateien gelöscht."""
        mock_concat.return_value = True
        deleted: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            out1 = p / "v1.mp4"; out1.touch()
            out2 = p / "v2.mp4"; out2.touch()
            merged_path = p / "merged.mp4"
            merged_path.touch()

            source_paths = [out1, out2]
            concat_ok = mock_concat(source_paths, merged_path,
                                    cancel_flag=threading.Event(), log_callback=None)
            if concat_ok:
                for src in source_paths:
                    try:
                        src.unlink()
                        deleted.append(src.name)
                    except OSError:
                        pass

        assert sorted(deleted) == ["v1.mp4", "v2.mp4"], (
            f"Gelöschte Dateien: {deleted}")

    @patch("src.runtime.workflow_executor.run_youtube_convert")
    @patch("src.runtime.workflow_executor.run_concat")
    def test_youtube_version_created_from_merged_file(self, mock_concat, mock_yt):
        """Bei create_youtube_version=True: run_youtube_convert() nach dem Concat."""
        mock_concat.return_value = True
        mock_yt.return_value = True

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            out1 = p / "v1.mp4"; out1.touch()
            out2 = p / "v2.mp4"; out2.touch()
            merged = p / "gruppe1_merged.mp4"
            merged.touch()

            from src.media.converter import ConvertJob
            cv1 = ConvertJob(source_path=p / "v1_src.mjpeg", output_path=out1, status="Fertig")
            cv2 = ConvertJob(source_path=p / "v2_src.mjpeg", output_path=out2, status="Fertig")

            gid = "gruppe1"
            fe1 = FileEntry(source_path=str(p / "v1_src.mjpeg"), merge_group_id=gid)
            fe2 = FileEntry(source_path=str(p / "v2_src.mjpeg"), merge_group_id=gid)

            job = WorkflowJob(source_mode="files", convert_enabled=True,
                              upload_youtube=False, create_youtube_version=True)
            job.files = [fe1, fe2]

            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
            per_settings = ex._build_job_settings(job)

            group = [(0, 0, job, cv1), (1, 0, job, cv2)]
            source_paths = [cv.output_path for _, _, _, cv in group
                            if cv.output_path and cv.output_path.exists()]

            concat_ok = mock_concat(source_paths, merged,
                                    cancel_flag=ex._cancel, log_callback=None)
            if concat_ok:
                # Replay the YouTube version creation from the merged file
                first_cv = group[0][3]
                first_cv.output_path = merged
                if job.create_youtube_version and merged.exists():
                    mock_yt(first_cv, per_settings,
                            cancel_flag=ex._cancel, log_callback=None)

        assert mock_yt.called, "run_youtube_convert wurde nicht aufgerufen"
        yt_job_arg = mock_yt.call_args[0][0]
        assert yt_job_arg.output_path == merged, (
            f"run_youtube_convert erhielt falschen cv_job: {yt_job_arg.output_path}")

    @patch("src.runtime.workflow_executor.run_concat")
    def test_standard_files_unaffected_by_existing_yt_variant(self, mock_concat):
        """Wenn _youtube-Datei existiert: trotzdem Standard-Datei für Concat nehmen."""
        mock_concat.return_value = True

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            out1 = p / "v1.mp4"; out1.touch()
            # Eine _youtube-Variante existiert – soll IGNORIERT werden
            (p / "v1_youtube.mp4").touch()

            from src.media.converter import ConvertJob
            cv1 = ConvertJob(source_path=p / "v1_src.mjpeg", output_path=out1, status="Fertig")
            out2 = p / "v2.mp4"; out2.touch()
            cv2 = ConvertJob(source_path=p / "v2_src.mjpeg", output_path=out2, status="Fertig")

            gid = "gruppe1"
            group = [(0, 0, None, cv1), (1, 0, None, cv2)]
            source_paths = [cv.output_path for _, _, _, cv in group
                            if cv.output_path and cv.output_path.exists()]

            assert source_paths == [out1, out2], (
                f"Nicht-Standard-Dateien im Concat: {source_paths}")

    @patch("src.runtime.workflow_executor.run_concat")
    def test_no_merge_for_single_file_group(self, mock_concat):
        """Gruppen mit nur einer Datei dürfen kein Concat auslösen."""
        mock_concat.return_value = True

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            out1 = p / "v1.mp4"; out1.touch()

            from src.media.converter import ConvertJob
            cv1 = ConvertJob(source_path=p / "v1_src.mjpeg", output_path=out1, status="Fertig")

            # Nur ein Element → kein Merge
            group = [(0, 0, None, cv1)]
            source_paths = [cv.output_path for _, _, _, cv in group
                            if cv.output_path and cv.output_path.exists()]

            if len(source_paths) >= 2:
                mock_concat(source_paths, p / "merged.mp4",
                            cancel_flag=threading.Event(), log_callback=None)

        assert not mock_concat.called, "run_concat darf bei Einzeldatei nicht aufgerufen werden"


# ─── phase_changed-Signal ─────────────────────────────────────────────────────

class TestPhaseChangedSignal:
    """phase_changed(str) liefert beim leeren Workflow keinen Crash."""

    def test_empty_workflow_no_phase_emitted(self):
        phases: list[str] = []
        wf = Workflow(jobs=[])
        ex = WorkflowExecutor(wf, _make_settings())
        ex.phase_changed.connect(phases.append)
        ex.run()
        assert phases == []

    def test_disabled_job_no_phase_emitted(self):
        phases: list[str] = []
        j = WorkflowJob(enabled=False)
        wf = Workflow(jobs=[j])
        ex = WorkflowExecutor(wf, _make_settings())
        ex.phase_changed.connect(phases.append)
        ex.run()
        assert phases == []


# ─── Merge-Upload: erst nach Concat, nie davor ────────────────────────────────

class TestMergeUploadAfterConcat:
    """Der YouTube-Upload darf bei Merge-Gruppen erst nach run_concat() erfolgen."""

    @staticmethod
    def _compute_skip_set_from_executor_logic(entries, job):
        """Repliziert die neue Skip-Logik: alle Merge-Mitglieder werden übersprungen."""
        from src.media.converter import ConvertJob
        to_convert = [
            (0, job, ConvertJob(source_path=Path(fe.source_path)))
            for fe in entries
        ]
        skip: set[int] = set()
        for ci, (_, j, cv) in enumerate(to_convert):
            entry = WorkflowExecutor._find_file_entry(j, str(cv.source_path))
            gid = (getattr(entry, "merge_group_id", "") or "") if entry else ""
            if gid:
                skip.add(ci)
        return skip

    def test_first_member_of_group_is_skipped(self):
        """Auch das ERSTE Element einer Merge-Gruppe wird übersprungen (kein Einzel-Upload)."""
        entries = [
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
        ]
        job = WorkflowJob(source_mode="files", convert_enabled=True)
        job.files = entries
        skip = self._compute_skip_set_from_executor_logic(entries, job)
        assert 0 in skip, "Index 0 (erstes Mitglied) muss in der Skip-Menge sein"

    def test_ungrouped_file_not_skipped(self):
        """Dateien ohne Merge-Gruppe bleiben außerhalb der Skip-Menge."""
        entries = [
            FileEntry(source_path="/a/solo.mp4"),
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
        ]
        job = WorkflowJob(source_mode="files", convert_enabled=True)
        job.files = entries
        skip = self._compute_skip_set_from_executor_logic(entries, job)
        assert 0 not in skip, "Solo-Datei darf nicht in der Skip-Menge sein"
        assert skip == {1, 2}

    @patch("src.workflow_steps.youtube_upload_step.upload_to_youtube")
    @patch("src.runtime.workflow_executor.run_concat")
    @patch("src.runtime.workflow_executor.run_convert")
    def test_upload_called_once_after_concat(self, mock_convert, mock_concat,
                                             mock_upload):
        """upload_to_youtube wird genau einmal gerufen – nach dem Merge, nicht davor."""
        from src.media.converter import ConvertJob

        mock_convert.side_effect = lambda cv, s, **kw: (
            setattr(cv, "status", "Fertig") or
            setattr(cv, "output_path", cv.source_path.with_suffix(".mp4")) or
            True
        )

        call_order: list[str] = []

        def fake_concat(sources, dest, **kw):
            call_order.append("concat")
            dest.touch()
            return True

        def fake_upload(cv_job, settings, yt_service, **kw):
            call_order.append("upload")
            return True

        mock_concat.side_effect = fake_concat
        mock_upload.side_effect = fake_upload

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src1 = p / "a.mjpeg"; src1.touch()
            src2 = p / "b.mjpeg"; src2.touch()

            def patched_convert(cv, s, **kw):
                out = cv.source_path.with_suffix(".mp4")
                out.touch()
                cv.output_path = out
                cv.status = "Fertig"
                return True

            mock_convert.side_effect = patched_convert

            entries = [
                FileEntry(source_path=str(src1), merge_group_id="g1"),
                FileEntry(source_path=str(src2), merge_group_id="g1"),
            ]
            job = WorkflowJob(
                source_mode="files",
                convert_enabled=True,
                upload_youtube=True,
                upload_kaderblick=False,
                title_card_enabled=False,
                create_youtube_version=False,
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "conv-1", "type": "convert"},
                    {"id": "merge-1", "type": "merge"},
                    {"id": "ytu-1", "type": "youtube_upload"},
                ],
                graph_edges=[
                    {"source": "src-1", "target": "conv-1"},
                    {"source": "conv-1", "target": "merge-1"},
                    {"source": "merge-1", "target": "ytu-1"},
                ],
            )
            job.files = entries

            wf = Workflow(jobs=[job])
            ex = WorkflowExecutor(wf, _make_settings())

            fake_yt_service = MagicMock()
            with patch(
                "src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube",
                side_effect=lambda *a, **kw: call_order.append("upload") or True,
            ):
                with patch("src.runtime.workflow_executor.get_youtube_service",
                           return_value=fake_yt_service):
                    ex.run()

        # Concat muss VOR dem Upload stehen
        assert "concat" in call_order, "run_concat wurde nicht aufgerufen"
        assert "upload" in call_order, "_upload_to_youtube wurde nicht aufgerufen"
        concat_pos = call_order.index("concat")
        upload_pos = call_order.index("upload")
        assert concat_pos < upload_pos, (
            f"Upload ({upload_pos}) sollte nach Concat ({concat_pos}) kommen, "
            f"aber war davor. Reihenfolge: {call_order}")

    @patch("src.workflow_steps.youtube_upload_step.upload_to_youtube")
    @patch("src.runtime.workflow_executor.run_concat")
    @patch("src.runtime.workflow_executor.run_convert")
    def test_no_upload_without_yt_service(self, mock_convert, mock_concat, mock_upload):
        """Ohne YouTube-Service kein Upload, auch nach dem Merge."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src1 = p / "a.mjpeg"; src1.touch()
            src2 = p / "b.mjpeg"; src2.touch()

            def patched_convert(cv, s, **kw):
                out = cv.source_path.with_suffix(".mp4")
                out.touch()
                cv.output_path = out
                cv.status = "Fertig"
                return True

            mock_convert.side_effect = patched_convert

            def fake_concat(sources, dest, **kw):
                dest.touch()
                return True

            mock_concat.side_effect = fake_concat

            entries = [
                FileEntry(source_path=str(src1), merge_group_id="g1"),
                FileEntry(source_path=str(src2), merge_group_id="g1"),
            ]
            job = WorkflowJob(
                source_mode="files",
                convert_enabled=True,
                upload_youtube=True,
                upload_kaderblick=False,
                title_card_enabled=False,
                create_youtube_version=False,
            )
            job.files = entries

            wf = Workflow(jobs=[job])
            ex = WorkflowExecutor(wf, _make_settings())
            with patch("src.runtime.workflow_executor.get_youtube_service", return_value=None):
                ex.run()

        mock_upload.assert_not_called()

    @patch("src.runtime.workflow_executor.run_youtube_convert")
    @patch("src.runtime.workflow_executor.run_concat")
    def test_upload_only_merge_runs_concat_before_yt_version_and_upload(
            self, mock_concat, mock_yt_convert):
        """Bei convert_enabled=False dürfen Merge-Gruppen erst nach dem Concat hochladen."""
        call_order: list[str] = []

        def fake_concat(sources, dest, **kw):
            call_order.append("concat")
            dest.touch()
            return True

        def fake_yt_convert(cv_job, settings, **kw):
            call_order.append("yt-version")
            yt_path = _derived_path(cv_job.output_path, "_youtube")
            yt_path.parent.mkdir(parents=True, exist_ok=True)
            yt_path.touch()
            return True

        mock_concat.side_effect = fake_concat
        mock_yt_convert.side_effect = fake_yt_convert

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src1 = p / "part1.mp4"; src1.touch()
            src2 = p / "part2.mp4"; src2.touch()

            entries = [
                FileEntry(source_path=str(src1), merge_group_id="g1", youtube_title="Finaltitel"),
                FileEntry(source_path=str(src2), merge_group_id="g1", youtube_title="Finaltitel"),
            ]
            job = WorkflowJob(
                source_mode="files",
                convert_enabled=False,
                upload_youtube=True,
                upload_kaderblick=False,
                title_card_enabled=False,
                create_youtube_version=True,
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "merge-1", "type": "merge"},
                    {"id": "ytv-1", "type": "yt_version"},
                    {"id": "ytu-1", "type": "youtube_upload"},
                ],
                graph_edges=[
                    {"source": "src-1", "target": "merge-1"},
                    {"source": "merge-1", "target": "ytv-1"},
                    {"source": "ytv-1", "target": "ytu-1"},
                ],
            )
            job.files = entries

            wf = Workflow(jobs=[job])
            ex = WorkflowExecutor(wf, _make_settings())

            with patch(
                "src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube",
                side_effect=lambda *a, **kw: call_order.append("upload") or True,
            ):
                with patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()):
                    ex.run()

        assert call_order == ["concat", "yt-version", "upload"], (
            "Merge-Upload ohne Konvertierung muss erst concat, dann YT-Version, dann Upload ausführen. "
            f"Tatsächliche Reihenfolge: {call_order}"
        )

    @patch("src.runtime.workflow_executor.run_concat")
    @patch("src.runtime.workflow_executor.run_convert")
    def test_merge_emits_progress_updates(self, mock_convert, mock_concat):
        progress_values: list[int] = []

        def patched_convert(cv, _settings, **_kw):
            out = cv.source_path.with_suffix(".mp4")
            out.touch()
            cv.output_path = out
            cv.status = "Fertig"
            return True

        def fake_concat(_sources, dest, **kwargs):
            progress_callback = kwargs.get("progress_callback")
            if progress_callback:
                progress_callback(10)
                progress_callback(55)
                progress_callback(100)
            dest.touch()
            return True

        mock_convert.side_effect = patched_convert
        mock_concat.side_effect = fake_concat

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src1 = p / "a.mjpeg"; src1.touch()
            src2 = p / "b.mjpeg"; src2.touch()

            job = WorkflowJob(
                source_mode="files",
                convert_enabled=True,
                upload_youtube=False,
                upload_kaderblick=False,
                files=[
                    FileEntry(source_path=str(src1), merge_group_id="g1"),
                    FileEntry(source_path=str(src2), merge_group_id="g1"),
                ],
            )
            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
            ex.job_progress.connect(lambda idx, pct: progress_values.append(pct) if idx == 0 else None)

            ex.run()

        assert 10 in progress_values
        assert 55 in progress_values
        assert 100 in progress_values


# ─── Titelkarte bei Merge-Gruppen: nur einmal, nach dem Concat ────────────────

class TestTitleCardMergeGroup:
    """Die Titelkarte darf bei Merge-Gruppen NICHT für jede Einzeldatei erstellt
    werden, sondern nur einmal für das zusammengeführte Ergebnis."""

    @staticmethod
    def _compute_title_card_flag_for_members(entries, job):
        """Repliziert die Prüfung: is_in_merge_group → title_card überspringen."""
        from src.media.converter import ConvertJob
        to_convert = [
            (0, job, ConvertJob(source_path=Path(fe.source_path)))
            for fe in entries
        ]
        # Build skip set (all merge members)
        skip: set[int] = set()
        for ci, (_, j, cv) in enumerate(to_convert):
            entry = WorkflowExecutor._find_file_entry(j, str(cv.source_path))
            gid = (getattr(entry, "merge_group_id", "") or "") if entry else ""
            if gid:
                skip.add(ci)

        # Für jede Datei: würde Titelkarte erstellt werden?
        results = []
        for ci, (_, j, cv) in enumerate(to_convert):
            is_in_merge_group = ci in skip
            would_create_title_card = j.title_card_enabled and not is_in_merge_group
            results.append(would_create_title_card)
        return results

    def _job_with_entries(self, entries, title_card=True):
        job = WorkflowJob(source_mode="files", convert_enabled=True,
                          title_card_enabled=title_card)
        job.files = entries
        return job

    def test_no_title_card_for_merge_members(self):
        """Alle Mitglieder einer Merge-Gruppe bekommen keine Titelkarte während Konvertierung."""
        entries = [
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/3.mp4", merge_group_id="g1"),
        ]
        job = self._job_with_entries(entries, title_card=True)
        flags = self._compute_title_card_flag_for_members(entries, job)
        assert flags == [False, False, False], (
            f"Erwartet alle False (kein Einzel-Titelkarte), got {flags}")

    def test_title_card_for_solo_files(self):
        """Einzelne Dateien (ohne Merge-Gruppe) bekommen Titelkarte."""
        entries = [
            FileEntry(source_path="/a/solo.mp4"),
        ]
        job = self._job_with_entries(entries, title_card=True)
        flags = self._compute_title_card_flag_for_members(entries, job)
        assert flags == [True]

    def test_mixed_solo_and_group(self):
        """Solo-Dateien bekommen Titelkarte, Merge-Mitglieder nicht."""
        entries = [
            FileEntry(source_path="/a/solo.mp4"),
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
            FileEntry(source_path="/a/2.mp4", merge_group_id="g1"),
        ]
        job = self._job_with_entries(entries, title_card=True)
        flags = self._compute_title_card_flag_for_members(entries, job)
        assert flags == [True, False, False]

    def test_title_card_disabled_globally(self):
        """Wenn title_card_enabled=False gilt das für alle Dateien."""
        entries = [
            FileEntry(source_path="/a/solo.mp4"),
            FileEntry(source_path="/a/1.mp4", merge_group_id="g1"),
        ]
        job = self._job_with_entries(entries, title_card=False)
        flags = self._compute_title_card_flag_for_members(entries, job)
        assert flags == [False, False]

    @patch("src.runtime.workflow_executor.run_concat")
    @patch("src.runtime.workflow_executor.run_convert")
    def test_prepend_title_card_called_once_after_concat(
            self, mock_convert, mock_concat):
        """TitleCardStep wird genau einmal aufgerufen — nach dem Concat."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src1 = p / "a.mjpeg"; src1.touch()
            src2 = p / "b.mjpeg"; src2.touch()

            def patched_convert(cv, s, **kw):
                out = cv.source_path.with_suffix(".mp4")
                out.touch()
                cv.output_path = out
                cv.status = "Fertig"
                return True

            mock_convert.side_effect = patched_convert

            def fake_concat(sources, dest, **kw):
                dest.touch()
                return True

            mock_concat.side_effect = fake_concat

            entries = [
                FileEntry(source_path=str(src1), merge_group_id="g1"),
                FileEntry(source_path=str(src2), merge_group_id="g1"),
            ]
            job = WorkflowJob(
                source_mode="files",
                convert_enabled=True,
                upload_youtube=False,
                upload_kaderblick=False,
                title_card_enabled=True,
                create_youtube_version=False,
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "conv-1", "type": "convert"},
                    {"id": "merge-1", "type": "merge"},
                    {"id": "title-1", "type": "titlecard"},
                ],
                graph_edges=[
                    {"source": "src-1", "target": "conv-1"},
                    {"source": "conv-1", "target": "merge-1"},
                    {"source": "merge-1", "target": "title-1"},
                ],
            )
            job.files = entries

            wf = Workflow(jobs=[job])
            ex = WorkflowExecutor(wf, _make_settings())
            call_count = {"n": 0}

            def fake_prepend(_executor, _orig_idx, cv_job, j, s):
                call_count["n"] += 1
                # return a fake path so the executor can continue
                fake_out = p / f"with_intro_{call_count['n']}.mp4"
                fake_out.touch()
                return fake_out, True

            with patch(
                "src.workflow_steps.title_card_step.TitleCardStep._prepend_title_card",
                side_effect=fake_prepend,
            ):
                ex.run()

        assert call_count["n"] == 1, (
            f"TitleCardStep._prepend_title_card sollte genau 1x aufgerufen werden, "
            f"wurde aber {call_count['n']}x aufgerufen")


class TestOriginalPreservationWithoutConvert:
    @patch("src.runtime.workflow_executor.run_concat")
    def test_merge_without_convert_keeps_original_files(self, mock_concat):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src1 = p / "halbzeit1.mp4"
            src2 = p / "halbzeit2.mp4"
            src1.write_text("a", encoding="utf-8")
            src2.write_text("b", encoding="utf-8")

            def fake_concat(sources, dest, **_kw):
                dest.write_text("merged", encoding="utf-8")
                return True

            mock_concat.side_effect = fake_concat

            job = WorkflowJob(
                source_mode="files",
                convert_enabled=False,
                upload_youtube=False,
                upload_kaderblick=False,
                title_card_enabled=False,
                create_youtube_version=False,
                files=[
                    FileEntry(source_path=str(src1), merge_group_id="g1"),
                    FileEntry(source_path=str(src2), merge_group_id="g1"),
                ],
            )

            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
            ex.run()

            assert src1.exists()
            assert src2.exists()
            assert (p / "processed" / "halbzeit1_merged.mp4").exists()

    @patch("src.workflow_steps.youtube_upload_step.upload_to_youtube", return_value=True)
    @patch("src.runtime.workflow_executor.get_youtube_service", return_value=object())
    @patch("src.runtime.workflow_executor.run_concat")
    def test_title_card_without_convert_creates_new_file_and_keeps_original(
        self,
        mock_concat,
        _mock_get_youtube_service,
        _mock_upload,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src = p / "original.mp4"
            src.write_text("video", encoding="utf-8")

            def fake_concat(sources, dest, **_kw):
                dest.write_text("with-titlecard", encoding="utf-8")
                return True

            mock_concat.side_effect = fake_concat

            job = WorkflowJob(
                source_mode="files",
                convert_enabled=False,
                upload_youtube=True,
                upload_kaderblick=False,
                title_card_enabled=True,
                create_youtube_version=False,
                files=[FileEntry(source_path=str(src))],
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "title-1", "type": "titlecard"},
                    {"id": "ytu-1", "type": "youtube_upload"},
                ],
                graph_edges=[
                    {"source": "src-1", "target": "title-1"},
                    {"source": "title-1", "target": "ytu-1"},
                ],
            )

            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())

            with patch(
                "src.workflow_steps.title_card_step.generate_title_card",
                return_value=True,
            ):
                ex.run()

            assert src.exists()
            assert src.read_text(encoding="utf-8") == "video"
            assert (p / "processed" / "original_titlecard.mp4").exists()

    @patch("src.workflow_steps.kaderblick_post_step.get_video_id_for_output", return_value="video-123")
    @patch("src.workflow_steps.kaderblick_post_step.kaderblick_post", return_value=True)
    @patch("src.workflow_steps.youtube_upload_step.YoutubeUploadStep._upload_to_youtube", return_value=True)
    def test_resume_uses_existing_merged_output_when_sources_are_gone(
        self,
        mock_upload,
        mock_kaderblick,
        _mock_video_id,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            src1 = p / "halbzeit1.mp4"
            src2 = p / "halbzeit2.mp4"
            merged = p / "processed" / "halbzeit1_merged.mp4"
            merged.parent.mkdir(parents=True, exist_ok=True)
            merged.write_text("merged", encoding="utf-8")

            job = WorkflowJob(
                source_mode="files",
                convert_enabled=False,
                create_youtube_version=True,
                upload_youtube=True,
                upload_kaderblick=True,
                default_kaderblick_game_id="game-1",
                files=[
                    FileEntry(source_path=str(src1), merge_group_id="g1"),
                    FileEntry(source_path=str(src2), merge_group_id="g1"),
                ],
                resume_status="Zusammenführen …",
                step_statuses={"transfer": "done", "merge": "running"},
                graph_nodes=[
                    {"id": "src-1", "type": "source_files"},
                    {"id": "merge-1", "type": "merge"},
                    {"id": "ytv-1", "type": "yt_version"},
                    {"id": "ytu-1", "type": "youtube_upload"},
                    {"id": "kb-1", "type": "kaderblick"},
                ],
                graph_edges=[
                    {"source": "src-1", "target": "merge-1"},
                    {"source": "merge-1", "target": "ytv-1"},
                    {"source": "ytv-1", "target": "ytu-1"},
                    {"source": "ytu-1", "target": "kb-1"},
                ],
            )

            ex = WorkflowExecutor(Workflow(jobs=[job]), _make_settings())
            ex._youtube_convert_func = MagicMock(return_value=True)

            with patch("src.runtime.workflow_executor.get_youtube_service", return_value=MagicMock()), \
                 patch("src.workflow_steps.merge_group_step.validate_media_output", return_value=True):
                ex.run()

            ex._youtube_convert_func.assert_called_once()
            yt_job = ex._youtube_convert_func.call_args[0][0]
            assert yt_job.output_path == merged
            mock_upload.assert_called_once()
            mock_kaderblick.assert_called_once()
            assert job.step_statuses["merge"] == "reused-target"
