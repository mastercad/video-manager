from collections import defaultdict
from pathlib import Path

from ...media.converter import ConvertJob
from ...settings import AppSettings
from ...workflow import FileEntry, WorkflowJob, graph_has_multiple_sources, graph_reachable_types, graph_source_nodes
from ...workflow_steps import ExecutorSupport, PreparedOutput
from ...integrations.kaderblick import publish_game_videos


class WorkflowExecutorSupportMixin:
    @staticmethod
    def _is_finished_step_status(status: str) -> bool:
        return status in {"done", "reused-target", "skipped"}

    def _planned_job_steps(self, job: WorkflowJob) -> list[str]:
        graph_types = {
            str(node.get("type", ""))
            for node in getattr(job, "graph_nodes", [])
            if isinstance(node, dict)
        }
        has_merge = any(file.merge_group_id for file in job.files) or "merge" in graph_types
        reachable_types = graph_reachable_types(job)
        convert_enabled = "convert" in reachable_types
        titlecard_enabled = "titlecard" in reachable_types
        youtube_version_enabled = "yt_version" in reachable_types
        youtube_upload_enabled = "youtube_upload" in reachable_types
        kaderblick_enabled = "kaderblick" in reachable_types

        steps = ["transfer"]
        if has_merge and self._merge_precedes_convert(job):
            steps.append("merge")
            if convert_enabled:
                steps.append("convert")
        else:
            if convert_enabled:
                steps.append("convert")
            if has_merge:
                steps.append("merge")
        if titlecard_enabled:
            steps.append("titlecard")
        for step in ("validate_surface", "validate_deep", "cleanup", "repair"):
            if step in reachable_types:
                steps.append(step)
        if youtube_version_enabled:
            steps.append("yt_version")
        if "stop" in reachable_types:
            steps.append("stop")
        if youtube_upload_enabled:
            steps.append("youtube_upload")
        if kaderblick_enabled:
            steps.append("kaderblick")

        seen: set[str] = set()
        result: list[str] = []
        for step in steps:
            if step in seen:
                continue
            seen.add(step)
            result.append(step)
        return result

    def _first_pending_step(self, job: WorkflowJob) -> str | None:
        for step in self._planned_job_steps(job):
            status = str(job.step_statuses.get(step, "") or "")
            if not self._is_finished_step_status(status):
                return step
        return None

    def _publish_completed_kaderblick_games(
        self,
        active: list[tuple[int, WorkflowJob]],
        run_failures: int,
    ) -> int:
        """Veröffentlicht erst, wenn der gesamte gestartete Job-Satz fertig ist."""
        if not bool(getattr(self._workflow, "publish_kaderblick_videos", False)):
            return 0
        if run_failures or self._cancel.is_set():
            return 0

        started_ids = set(getattr(self._workflow, "started_job_ids", []) or [])
        if not started_ids:
            started_ids = {job.id for _index, job in active}
            self._workflow.started_job_ids = sorted(started_ids)

        active_ids = {job.id for _index, job in active}
        cohort = [job for job in self._workflow.jobs if job.id in started_ids]
        if len(cohort) != len(started_ids):
            self.log_message.emit("⚠ Kaderblick-Publish ausstehend: Ein gestarteter Job fehlt im Workflow.")
            return 0

        for job in cohort:
            # Aktive Jobs haben die Pipeline an dieser Stelle bereits ohne Fehler
            # beendet. Ihre per Signal aktualisierte resume_status-Anzeige kann im
            # GUI-Thread noch hinterherhinken und darf den Publish nicht blockieren.
            if job.id in active_ids:
                continue
            if not str(job.resume_status or "").startswith("Fertig"):
                self.log_message.emit(
                    f"⚠ Kaderblick-Publish ausstehend: '{job.name}' gehört zum gestarteten Lauf, "
                    "ist aber noch nicht fertig."
                )
                return 0

        game_ids: set[str] = set()
        for job in cohort:
            if not self._support.job_reaches_type(job, "kaderblick"):
                continue
            explicit_ids = [entry.kaderblick_game_id for entry in job.files] if job.files else [""]
            for explicit_id in explicit_ids:
                game_id = ExecutorSupport.resolve_kaderblick_game_id(self._settings, job, explicit_id)
                if game_id:
                    game_ids.add(game_id)

        if not game_ids:
            self.log_message.emit(
                "⚠ Kaderblick-Publish nicht ausgeführt: Für die fertigen Kaderblick-Jobs "
                "ist keine Spiel-ID konfiguriert."
            )
            return 0

        statuses = self._workflow.kaderblick_publish_statuses
        failures = 0
        for game_id in sorted(game_ids):
            if statuses.get(game_id) == "done":
                continue
            self.log_message.emit(f"📣 Kaderblick: Videos für Spiel {game_id} veröffentlichen …")
            try:
                publish_game_videos(self._settings.kaderblick, game_id)
            except RuntimeError as exc:
                statuses[game_id] = "error"
                failures += 1
                self.log_message.emit(f"❌ Kaderblick-Publish für Spiel {game_id} fehlgeschlagen: {exc}")
            else:
                statuses[game_id] = "done"
                self.log_message.emit(f"✅ Kaderblick: Videos für Spiel {game_id} veröffentlicht")
        return failures

    def _step_precedes(self, job: WorkflowJob, step: str, target_step: str) -> bool:
        planned_steps = self._planned_job_steps(job)
        if step not in planned_steps or target_step not in planned_steps:
            return False
        return planned_steps.index(step) < planned_steps.index(target_step)

    def _should_skip_step_for_resume(
        self,
        job: WorkflowJob,
        step: str,
        resume_step: str | None = None,
    ) -> bool:
        if not self._allow_reuse_existing:
            return False
        current_resume_step = resume_step or self._first_pending_step(job)
        if not current_resume_step or current_resume_step == step:
            return False
        status = str(job.step_statuses.get(step, "") or "")
        return self._is_finished_step_status(status) and self._step_precedes(job, step, current_resume_step)

    def _build_pipeline_kaderblick_sort_index(
        self,
        active: list[tuple[int, WorkflowJob]],
    ) -> dict[tuple[str, str], int]:
        kb_by_game: dict[str, list[str]] = defaultdict(list)
        for _index, job in active:
            if not self._support.job_reaches_type(job, "kaderblick"):
                continue

            candidates: list[tuple[str, str]] = []
            if job.files:
                for entry in job.files:
                    game_id = ExecutorSupport.resolve_kaderblick_game_id(self._settings, job, entry.kaderblick_game_id)
                    name = Path(entry.source_path).name if entry.source_path else ""
                    if game_id and name:
                        candidates.append((game_id, name))
            elif job.source_mode == "folder_scan" and job.source_folder:
                src_dir = Path(job.source_folder)
                pattern = job.file_pattern or "*.mp4"
                if src_dir.exists():
                    for path in sorted(src_dir.glob(pattern)):
                        if not path.is_file():
                            continue
                        game_id = ExecutorSupport.resolve_kaderblick_game_id(self._settings, job)
                        if game_id:
                            candidates.append((game_id, path.name))

            for game_id, name in candidates:
                kb_by_game[game_id].append(name)

        kb_sort_index: dict[tuple[str, str], int] = {}
        for game_id, names in kb_by_game.items():
            for pos, name in enumerate(sorted(set(names)), start=1):
                kb_sort_index[(game_id, name)] = pos
        return kb_sort_index

    def _estimate_pipeline_total_steps(self, active: list[tuple[int, WorkflowJob]]) -> int:
        total = len(active)
        for _index, job in active:
            file_count = self._estimate_job_file_count(job)
            merge_groups = {
                entry.merge_group_id
                for entry in job.files
                if getattr(entry, "merge_group_id", "")
            }
            merge_member_count = len([
                entry for entry in job.files if getattr(entry, "merge_group_id", "")
            ])
            merge_before_convert = self._merge_precedes_convert(job)

            if self._support.job_reaches_type(job, "convert"):
                if merge_before_convert and merge_groups:
                    total += max(file_count - merge_member_count, 0)
                    total += len(merge_groups)
                else:
                    total += file_count
            total += len(merge_groups)

            if self._support.job_reaches_type(job, "repair"):
                total += len(merge_groups) if merge_groups else max(1, file_count)

            if self._support.job_reaches_type(job, "youtube_upload"):
                total += len(merge_groups) if merge_groups else max(1, file_count)
        return total

    @staticmethod
    def _estimate_job_file_count(job: WorkflowJob) -> int:
        if graph_has_multiple_sources(job):
            total = 0
            source_ids = {node_id for node_id, _node_type in graph_source_nodes(job)}
            total += len([
                entry for entry in job.files if not entry.graph_source_id or entry.graph_source_id in source_ids
            ])
            if any(node_type == "source_folder_scan" for _node_id, node_type in graph_source_nodes(job)) and job.source_folder:
                src_dir = Path(job.source_folder)
                pattern = job.file_pattern or "*.mp4"
                if src_dir.exists():
                    total += len([path for path in src_dir.glob(pattern) if path.is_file()])
            if any(node_type == "source_pi_download" for _node_id, node_type in graph_source_nodes(job)) and job.files:
                total += len([entry for entry in job.files if entry.graph_source_id])
            return max(total, 1)
        if job.files:
            return len(job.files)
        if job.source_mode == "folder_scan" and job.source_folder:
            src_dir = Path(job.source_folder)
            pattern = job.file_pattern or "*.mp4"
            if src_dir.exists():
                return len([path for path in src_dir.glob(pattern) if path.is_file()])
        return 1

    def _build_convert_job(self, job: WorkflowJob, file_path: str) -> ConvertJob:
        return self._support.build_convert_job(self, job, file_path)

    @staticmethod
    def _find_file_entry(job: WorkflowJob, file_path: str) -> FileEntry | None:
        return ExecutorSupport.find_file_entry(job, file_path)

    @staticmethod
    def _register_runtime_file_entry(job: WorkflowJob, source_node_id: str, file_path: str) -> FileEntry:
        return ExecutorSupport.register_runtime_file_entry(job, source_node_id, file_path)

    @classmethod
    def _get_merge_group_id(cls, job: WorkflowJob, file_path: str) -> str:
        return ExecutorSupport.get_merge_group_id(job, file_path)

    def _resolve_youtube_title(self, job: WorkflowJob | str, file_path: str | None = None) -> str:
        if isinstance(self, WorkflowJob):
            return ExecutorSupport.resolve_youtube_title(self, str(job), settings=None)
        return ExecutorSupport.resolve_youtube_title(job, str(file_path or ""), settings=getattr(self, "_settings", None))

    def _resolve_youtube_playlist(self, job: WorkflowJob, file_path: str) -> str:
        return ExecutorSupport.resolve_youtube_playlist(job, file_path, settings=self._settings)

    def _resolve_youtube_description(self, job: WorkflowJob, file_path: str) -> str:
        return ExecutorSupport.resolve_youtube_description(job, file_path, settings=self._settings)

    def _resolve_youtube_tags(self, job: WorkflowJob, file_path: str) -> list[str]:
        return ExecutorSupport.resolve_youtube_tags(job, file_path, settings=self._settings)

    def _build_job_settings(self, job: WorkflowJob) -> AppSettings:
        return self._support.build_job_settings(self, job)

    def _merge_precedes_convert(self, job: WorkflowJob) -> bool:
        return self._support.merge_precedes_convert(job)

    def _prepared_output_reaches_type(self, prepared: PreparedOutput, target_type: str) -> bool:
        return self._support.prepared_output_reaches_type(prepared, target_type)

    def _advance_prepared_output_cursor(self, prepared: PreparedOutput, step_name: str) -> None:
        self._support.advance_prepared_output_cursor(prepared, step_name)

    def _graph_node_id_for_type(self, job: WorkflowJob, node_type: str) -> str:
        return self._support.graph_node_id_for_type(job, node_type)

    def _validation_branch_has_targets(self, prepared: PreparedOutput, node_type: str, branch: str) -> bool:
        return self._support.validation_branch_has_targets(prepared, node_type, branch)

    def _get_youtube_service(self):
        from . import get_youtube_service

        return get_youtube_service(log_callback=self.log_message.emit)

    def _set_job_status(self, orig_idx: int, status: str) -> None:
        is_transfer_status = status.startswith("Transfer")
        active_job = None
        if 0 <= orig_idx < len(getattr(self._workflow, "jobs", [])):
            active_job = self._workflow.jobs[orig_idx]

        if not (
            is_transfer_status
            and active_job is not None
            and getattr(active_job, "current_step_key", "") not in {"", "transfer"}
        ):
            self.job_status.emit(orig_idx, status)
        if is_transfer_status:
            self.source_status.emit(orig_idx, status)

    @staticmethod
    def _set_step_status(job: WorkflowJob, step: str, status: str) -> None:
        if not isinstance(job.step_statuses, dict):
            job.step_statuses = {}
        job.step_statuses[step] = status
        job.current_step_key = step

    @staticmethod
    def _set_step_detail(job: WorkflowJob, step: str, detail: str) -> None:
        if not isinstance(job.step_details, dict):
            job.step_details = {}
        job.step_details[step] = detail
