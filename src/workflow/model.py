"""Workflow-Datenmodell."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import uuid


_TRANSIENT_JOB_FIELDS = {
    "status",
    "error_msg",
}

_SESSION_JOB_FIELDS = {
    "resume_status",
    "step_statuses",
    "step_details",
    "progress_pct",
    "overall_progress_pct",
    "current_step_key",
    "transfer_status",
    "transfer_progress_pct",
    "run_started_at",
    "run_finished_at",
    "run_elapsed_seconds",
}

_SESSION_WORKFLOW_FIELDS = {
    "last_run_started_at",
    "last_run_finished_at",
    "last_run_elapsed_seconds",
    "started_job_ids",
    "kaderblick_publish_statuses",
}


@dataclass
class FileEntry:
    """Metadaten fuer eine einzelne Quelldatei innerhalb eines Auftrags."""

    source_path: str = ""
    source_size_bytes: int = 0
    output_filename: str = ""
    youtube_title: str = ""
    youtube_description: str = ""
    youtube_playlist: str = ""
    kaderblick_game_id: str = ""
    kaderblick_game_start: int = 0
    kaderblick_video_type_id: int = 0
    kaderblick_camera_id: int = 0
    merge_group_id: str = ""
    title_card_subtitle: str = ""
    graph_source_id: str = ""
    title_card_before_merge: bool = False


@dataclass
class WorkflowJob:
    """Ein einzelner Auftrag im Workflow."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    enabled: bool = True
    name: str = ""

    source_mode: str = "files"
    files: list = field(default_factory=list)

    device_name: str = ""
    download_destination: str = ""
    delete_after_download: bool = False

    source_folder: str = ""
    file_pattern: str = "*.mp4"
    copy_destination: str = ""
    move_files: bool = False
    output_prefix: str = ""

    convert_enabled: bool = True
    encoder: str = "auto"
    crf: int = 18
    preset: str = "medium"
    no_bframes: bool = True
    fps: int = 25
    output_format: str = "mp4"
    output_resolution: str = "source"
    overwrite: bool = False

    merge_audio: bool = False
    amplify_audio: bool = False
    amplify_db: float = 6.0
    audio_sync: bool = False

    create_youtube_version: bool = False
    upload_youtube: bool = False
    default_youtube_title: str = ""
    default_youtube_playlist: str = ""
    default_youtube_description: str = ""
    default_youtube_competition: str = ""
    youtube_match_data: dict = field(default_factory=dict)
    youtube_segment_data: dict = field(default_factory=dict)
    youtube_kaderblick_video_type_id: int = 0
    youtube_kaderblick_camera_id: int = 0
    merge_output_title: str = ""
    merge_output_playlist: str = ""
    merge_output_description: str = ""
    merge_match_data: dict = field(default_factory=dict)
    merge_segment_data: dict = field(default_factory=dict)
    merge_output_kaderblick_video_type_id: int = 0
    merge_output_kaderblick_camera_id: int = 0
    merge_encoder: str = "inherit"
    merge_crf: int = 0
    merge_preset: str = "medium"
    merge_no_bframes: bool = True
    merge_fps: int = 0
    merge_output_format: str = "source"
    merge_output_resolution: str = "source"

    upload_kaderblick: bool = False
    default_kaderblick_game_id: str = ""
    default_kaderblick_video_type_id: int = 0
    default_kaderblick_camera_id: int = 0
    yt_version_encoder: str = "inherit"
    yt_version_crf: int = 0
    yt_version_preset: str = "medium"
    yt_version_no_bframes: bool = True
    yt_version_fps: int = 0
    yt_version_output_format: str = "source"
    yt_version_output_resolution: str = "source"

    title_card_enabled: bool = False
    title_card_logo_path: str = ""
    title_card_duration: float = 3.0
    title_card_bg_color: str = "#000000"
    title_card_fg_color: str = "#FFFFFF"
    title_card_home_team: str = ""
    title_card_away_team: str = ""
    title_card_date: str = ""
    graph_nodes: list = field(default_factory=list)
    graph_edges: list = field(default_factory=list)

    resume_status: str = ""
    step_statuses: dict = field(default_factory=dict)
    step_details: dict = field(default_factory=dict)

    status: str = "Wartend"
    progress_pct: int = 0
    overall_progress_pct: int = 0
    current_step_key: str = ""
    error_msg: str = ""
    transfer_status: str = ""
    transfer_progress_pct: int = 0
    run_started_at: str = ""
    run_finished_at: str = ""
    run_elapsed_seconds: float = 0.0

    def to_dict(self, *, include_runtime: bool = False) -> dict:
        payload = asdict(self)
        excluded_fields = set(_TRANSIENT_JOB_FIELDS)
        if not include_runtime:
            excluded_fields.update(_SESSION_JOB_FIELDS)
        for key in excluded_fields:
            payload.pop(key, None)
        return payload

    @classmethod
    def from_dict(cls, data: dict, *, include_runtime: bool = False) -> "WorkflowJob":
        valid = set(cls.__dataclass_fields__)
        excluded_fields = set(_TRANSIENT_JOB_FIELDS)
        if not include_runtime:
            excluded_fields.update(_SESSION_JOB_FIELDS)
        filtered = {
            key: value
            for key, value in data.items()
            if key in valid and key not in excluded_fields
        }
        raw_files = filtered.pop("files", [])
        files = [
            FileEntry(**{k: v for k, v in entry.items() if k in FileEntry.__dataclass_fields__})
            for entry in raw_files
        ]
        return cls(files=files, **filtered)


def workflow_output_device_name(job: WorkflowJob) -> str:
    explicit_device = (job.device_name or "").strip()
    if explicit_device:
        return explicit_device

    for segment_data in (job.merge_segment_data, job.youtube_segment_data):
        if not isinstance(segment_data, dict):
            continue
        camera = str(segment_data.get("camera") or "").strip()
        if camera and camera not in {"–", "(Keine Kamera)"}:
            return camera

    return ""


@dataclass(init=False)
class Workflow:
    """Kompletter Workflow: Liste von Workflow-Jobs plus globale Optionen."""

    name: str = ""
    jobs: list[WorkflowJob] = field(default_factory=list)
    shutdown_after: bool = False
    publish_kaderblick_videos: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_run_started_at: str = ""
    last_run_finished_at: str = ""
    last_run_elapsed_seconds: float = 0.0
    started_job_ids: list[str] = field(default_factory=list)
    kaderblick_publish_statuses: dict[str, str] = field(default_factory=dict)

    def __init__(
        self,
        name: str = "",
        job: WorkflowJob | None = None,
        jobs: list[WorkflowJob] | None = None,
        shutdown_after: bool = False,
        publish_kaderblick_videos: bool = False,
        created_at: str | None = None,
        last_run_started_at: str = "",
        last_run_finished_at: str = "",
        last_run_elapsed_seconds: float = 0.0,
        started_job_ids: list[str] | None = None,
        kaderblick_publish_statuses: dict[str, str] | None = None,
    ):
        self.name = name
        if jobs is not None:
            self.jobs = list(jobs)
        elif job is not None:
            self.jobs = [job]
        else:
            self.jobs = []
        self.shutdown_after = shutdown_after
        self.publish_kaderblick_videos = bool(publish_kaderblick_videos)
        self.created_at = created_at or datetime.now().isoformat(timespec="seconds")
        self.last_run_started_at = last_run_started_at
        self.last_run_finished_at = last_run_finished_at
        self.last_run_elapsed_seconds = float(last_run_elapsed_seconds or 0.0)
        self.started_job_ids = list(started_job_ids or [])
        self.kaderblick_publish_statuses = dict(kaderblick_publish_statuses or {})

    @property
    def job(self) -> WorkflowJob | None:
        return self.jobs[0] if self.jobs else None

    @job.setter
    def job(self, value: WorkflowJob | None) -> None:
        if value is None:
            self.jobs = []
            return
        if self.jobs:
            self.jobs[0] = value
        else:
            self.jobs = [value]

    def to_dict(self, *, include_runtime: bool = False) -> dict:
        payload = {
            "name": self.name,
            "shutdown_after": self.shutdown_after,
            "publish_kaderblick_videos": self.publish_kaderblick_videos,
            "created_at": self.created_at,
            "job": self.job.to_dict(include_runtime=include_runtime) if self.job is not None else None,
            "jobs": [job.to_dict(include_runtime=include_runtime) for job in self.jobs],
        }
        if include_runtime:
            payload.update(
                {
                    "last_run_started_at": self.last_run_started_at,
                    "last_run_finished_at": self.last_run_finished_at,
                    "last_run_elapsed_seconds": self.last_run_elapsed_seconds,
                    "started_job_ids": list(self.started_job_ids),
                    "kaderblick_publish_statuses": dict(self.kaderblick_publish_statuses),
                }
            )
        return payload

    @classmethod
    def from_dict(cls, data: dict, *, include_runtime: bool = False) -> "Workflow":
        from .migration import _migrate_source_to_job

        raw_jobs = data.get("jobs", [])
        if not raw_jobs:
            raw_job = data.get("job")
            if raw_job is not None:
                raw_jobs = [raw_job]

        if not raw_jobs and "sources" in data and data["sources"]:
            raw_jobs = [_migrate_source_to_job(source) for source in data["sources"]]

        jobs: list[WorkflowJob] = []
        for raw_job in raw_jobs:
            if isinstance(raw_job, dict):
                jobs.append(WorkflowJob.from_dict(raw_job, include_runtime=include_runtime))
        return cls(
            name=data.get("name", ""),
            jobs=jobs,
            shutdown_after=data.get("shutdown_after", False),
            publish_kaderblick_videos=data.get("publish_kaderblick_videos", False),
            created_at=data.get("created_at", ""),
            last_run_started_at=data.get("last_run_started_at", "") if include_runtime else "",
            last_run_finished_at=data.get("last_run_finished_at", "") if include_runtime else "",
            last_run_elapsed_seconds=data.get("last_run_elapsed_seconds", 0.0) if include_runtime else 0.0,
            started_job_ids=data.get("started_job_ids", []) if include_runtime else [],
            kaderblick_publish_statuses=data.get("kaderblick_publish_statuses", {}) if include_runtime else {},
        )

    def save(self, path: Path) -> None:
        from .storage import save_workflow

        save_workflow(self, path)

    @classmethod
    def load(cls, path: Path) -> "Workflow":
        from .storage import load_workflow

        return load_workflow(path)

    def save_as_last(self) -> None:
        from .storage import LAST_WORKFLOW_FILE, save_workflow

        save_workflow(self, LAST_WORKFLOW_FILE, include_runtime=True)

    @classmethod
    def load_last(cls) -> "Workflow | None":
        from .storage import load_last_workflow

        return load_last_workflow()
