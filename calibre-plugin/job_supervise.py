# -*- coding: utf-8 -*-
"""Watch detached ao3kit jobs and run Calibre ingest when they finish."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from PyQt5.Qt import QTimer

from calibre.gui2 import error_dialog

from calibre_plugins.fanfic_organizer.cleaned import canonical_work_id
from calibre_plugins.fanfic_organizer.columns import apply_layout_columns
from calibre_plugins.fanfic_organizer.enrich import run_ao3kit
from calibre_plugins.fanfic_organizer.epub_plan import (
    merge_download_manifest,
    pending_epub_attachments,
    pending_incremental_imports,
    summarize_epub_download,
)
from calibre_plugins.fanfic_organizer.importer import (
    attach_downloaded_epubs,
    import_record,
    iter_identifier_maps,
    refresh_library_ui,
)
from calibre_plugins.fanfic_organizer.job_plans import (
    apply_identify_choices,
    load_identify_jsonl,
    merge_identify_ready,
    merge_ready_with_jsonl,
    plan_fill_from_ao3,
    split_identify_records,
)
from calibre_plugins.fanfic_organizer.scrape_run import scrape_record_failed
from calibre_plugins.fanfic_organizer.job_ui import JobLogDialog, JobNotifyDialog
from calibre_plugins.fanfic_organizer.jobs import (
    first_line,
    job_is_retryable,
    job_paths,
    job_was_notified,
    jobs_root,
    new_job_id,
    parse_job_list_json,
    parse_job_status_json,
    read_json,
    write_json,
)
from calibre_plugins.fanfic_organizer.jsonl_loader import load_jsonl_records, resolve_epub_path
from calibre_plugins.fanfic_organizer.progress import (
    _book_noun,
    _finish_with_collections,
    _finish_with_remaps,
    write_import_payload,
)
from calibre_plugins.fanfic_organizer.scrape_run import (
    build_job_clear_argv,
    build_job_delete_argv,
    build_job_list_argv,
    build_job_retry_argv,
    build_job_start_argv,
    build_job_stop_argv,
)
from calibre_plugins.fanfic_organizer.selected import (
    apply_cleaned_records,
    apply_collections_records,
    apply_cover_records,
    apply_series_records,
    book_has_epub,
    copy_book_epub,
    stamp_ao3_identifiers,
)


class JobSupervisor:
    """Owns detached jobs: attach logs, stop, Calibre writeback."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.gui = plugin.gui
        self._dialogs: dict[str, JobLogDialog] = {}
        self._list_dialog = None
        self._ingesting: set[str] = set()
        self._epub_seen: dict[str, set[Any]] = {}
        self._import_seen: dict[str, dict[str, dict[str, Any]]] = {}
        self._timer = QTimer(plugin.gui)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    GRAPH_JOB_ID = 'graph'

    def _project(self) -> Path | None:
        cached = getattr(self, '_project_path', None)
        if cached is not None:
            return cached
        from calibre_plugins.fanfic_organizer.enrich import resolve_ao3kit_runtime

        project, _python, error = resolve_ao3kit_runtime()
        if error or project is None:
            return None
        self._project_path = Path(project)
        return self._project_path

    def jobs_dir(self) -> Path | None:
        project = self._project()
        if project is None:
            return None
        return jobs_root(project)

    def _jobs_dir_arg(self) -> str | None:
        root = self.jobs_dir()
        return str(root) if root is not None else None

    def list_jobs(self) -> list[dict[str, Any]]:
        code, stdout, _stderr = run_ao3kit(
            build_job_list_argv(jobs_dir=self._jobs_dir_arg())
        )
        if code != 0:
            return self._list_jobs_from_disk()
        parsed = parse_job_list_json(stdout)
        jobs = list(parsed) if parsed else self._list_jobs_from_disk()
        if not any(str(item.get('id') or '') == 'warm' for item in jobs):
            warm = self._warm_status()
            if warm is not None:
                jobs.append(warm)
        return jobs

    def _list_jobs_from_disk(self) -> list[dict[str, Any]]:
        root = self.jobs_dir()
        jobs: list[dict[str, Any]] = []
        if root is not None and root.is_dir():
            for child in sorted(root.iterdir(), reverse=True):
                status = read_json(child / 'status.json')
                spec = read_json(child / 'spec.json') or {}
                if status is None and not spec:
                    continue
                row = dict(status or {})
                row.setdefault('id', child.name)
                row.setdefault('title', spec.get('title') or child.name)
                row.setdefault('kind', spec.get('kind') or '')
                jobs.append(row)
        warm = self._warm_status()
        if warm is not None:
            jobs.append(warm)
        return jobs

    def _warm_status(self) -> dict[str, Any] | None:
        project = self._project()
        if project is None:
            return None
        from calibre_plugins.fanfic_organizer.tag_warm import (
            read_status_file,
            warm_log_path,
            warm_status_path,
        )

        status = read_status_file(warm_status_path(project))
        if not status:
            return None
        return {
            'id': 'warm',
            'title': 'Background tag cache',
            'kind': 'tags.warm',
            'running': bool(status.get('running')),
            'pid': status.get('pid'),
            'message': status.get('message') or '',
            'log_path': str(warm_log_path(project)),
            'ingest': 'none',
            'result': (
                f"{status.get('cached') or 0}/{status.get('source_count') or 0} cached"
                if status.get('source_count')
                else (status.get('message') or '')
            ),
        }

    def prepare_job_dir(self, kind: str) -> Path | None:
        root = self.jobs_dir()
        if root is None:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Could not find ao3kit. Install fanfic-organizer.zip from GitHub '
                'Releases, or set Project path in plugin settings.',
                show=True,
            )
            return None
        job_dir = root / new_job_id(kind)
        (job_dir / 'work').mkdir(parents=True, exist_ok=True)
        return job_dir

    def start_prepared(
        self, job_dir: Path, *, attach: bool = True, quiet: bool = False
    ) -> str | None:
        code, stdout, stderr = run_ao3kit(
            build_job_start_argv(str(job_dir), jobs_dir=self._jobs_dir_arg())
        )
        status = parse_job_status_json(stdout) or {}
        job_id = str(status.get('id') or job_dir.name)
        if code != 0 and not status.get('running'):
            if not quiet:
                error_dialog(
                    self.gui,
                    'Fanfic Organizer',
                    'Could not start background job.',
                    det_msg=(stderr or stdout or f'exit {code}').strip(),
                    show=True,
                )
            return None
        if attach:
            self.attach(job_id)
        return job_id

    def ensure_graph_server(self) -> str | None:
        """Start the live tag-graph job if needed. Returns the viewer URL."""
        import time

        from calibre_plugins.fanfic_organizer.graph_live import (
            GRAPH_JOB_ID,
            read_serve_url,
        )
        from calibre_plugins.fanfic_organizer.job_plans import plan_graph_serve

        project = self._project()
        if project is None:
            return None
        url = read_serve_url(project)
        if url:
            return url
        root = self.jobs_dir()
        if root is None:
            return None
        job_dir = root / GRAPH_JOB_ID
        (job_dir / 'work').mkdir(parents=True, exist_ok=True)
        plan_graph_serve(job_dir)
        self.start_prepared(job_dir, attach=False, quiet=True)
        for _ in range(24):
            url = read_serve_url(project)
            if url:
                return url
            time.sleep(0.25)
        return read_serve_url(project)

    def _drain_graph_commands(self) -> None:
        project = self._project()
        if project is None:
            return
        from calibre_plugins.fanfic_organizer.graph_live import (
            mark_graph_command_done,
            mark_graph_command_error,
            pending_graph_commands,
        )
        from calibre_plugins.fanfic_organizer.job_plans import plan_scrape
        from calibre_plugins.fanfic_organizer.prefs import plugin_runtime_settings, prefs
        from calibre_plugins.fanfic_organizer.scrape_run import merge_plugin_settings

        pending = pending_graph_commands(project)
        if not pending:
            return
        for path, cmd in pending:
            if str(cmd.get('kind') or '') != 'similar':
                mark_graph_command_error(path, 'unknown command')
                continue
            options = merge_plugin_settings(
                dict(cmd.get('options') or {}),
                plugin_runtime_settings(),
            )
            options['download_epubs'] = bool(prefs.get('download_epubs', True))
            options['simplify_tags'] = bool(prefs.get('simplify_tags', False))
            options['update_existing'] = bool(prefs.get('update_existing', True))
            options['max_results'] = (
                str(options.get('max_results') or '').strip()
                or str(prefs.get('last_max_results') or '25')
            )
            job_dir = self.prepare_job_dir('scrape')
            if job_dir is None:
                return
            spec = plan_scrape(options, job_dir)
            titles = cmd.get('titles') or cmd.get('work_ids') or []
            label = str(titles[0] if titles else 'graph')
            spec['title'] = f'Search similar: {label}'[:80]
            write_json(job_dir / 'spec.json', spec)
            job_id = self.start_prepared(job_dir, attach=False, quiet=True)
            if job_id:
                mark_graph_command_done(path)
            else:
                mark_graph_command_error(path, 'Could not start scrape job')

    def attach(self, job_id: str) -> None:
        existing = self._dialogs.get(job_id)
        if existing is not None:
            try:
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._dialogs.pop(job_id, None)
        log_path, status_path, title = self._paths_for(job_id)
        if log_path is None or status_path is None:
            error_dialog(self.gui, 'Fanfic Organizer', f'Unknown job {job_id}.', show=True)
            return
        dialog = JobLogDialog(
            self.gui,
            job_id=job_id,
            title=title or 'Fanfic Organizer — Job',
            log_path=log_path,
            status_path=status_path,
            supervisor=self,
        )
        dialog.destroyed.connect(lambda *_a, jid=job_id: self._dialogs.pop(jid, None))
        self._dialogs[job_id] = dialog
        dialog.show()

    def detach(self, job_id: str) -> None:
        dialog = self._dialogs.pop(job_id, None)
        if dialog is not None:
            try:
                dialog._closing = True
                dialog.close()
            except RuntimeError:
                pass

    def cancel(self, job_id: str) -> None:
        run_ao3kit(build_job_stop_argv(job_id, jobs_dir=self._jobs_dir_arg()))

    def retry(self, job_id: str, *, attach: bool = True) -> str | None:
        if job_id == 'warm':
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'The tag-cache warmer cannot be retried from this list. '
                'Use Tags and collections → Warm tag cache.',
                show=True,
            )
            return None
        self._epub_seen.pop(job_id, None)
        self._import_seen.pop(job_id, None)
        self._ingesting.discard(job_id)
        code, stdout, stderr = run_ao3kit(
            build_job_retry_argv(job_id, jobs_dir=self._jobs_dir_arg())
        )
        status = parse_job_status_json(stdout) or {}
        if code != 0 and not status.get('running'):
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Could not retry job.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return None
        dialog = self._dialogs.get(job_id)
        if dialog is not None:
            try:
                dialog.mark_retrying()
            except RuntimeError:
                self._dialogs.pop(job_id, None)
                dialog = None
        if attach and dialog is None:
            self.attach(job_id)
        return job_id

    def forget_job(self, job_id: str) -> None:
        self.detach(job_id)
        self._epub_seen.pop(job_id, None)
        self._import_seen.pop(job_id, None)
        self._ingesting.discard(job_id)

    def delete_jobs(self, job_ids: list[str]) -> list[str]:
        ids = [str(job_id).strip() for job_id in job_ids if str(job_id).strip()]
        if any(job_id == 'warm' for job_id in ids):
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'The tag-cache warmer cannot be deleted from this list. '
                'Use Stop background tag cache…',
                show=True,
            )
            ids = [job_id for job_id in ids if job_id != 'warm']
        if not ids:
            return []
        code, stdout, stderr = run_ao3kit(
            build_job_delete_argv(ids, jobs_dir=self._jobs_dir_arg())
        )
        payload = parse_job_status_json(stdout) or {}
        deleted = [str(item) for item in (payload.get('deleted') or [])]
        for job_id in deleted:
            self.forget_job(job_id)
        errors = payload.get('errors') or []
        if code != 0 and not deleted:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Could not delete job.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
            return []
        if errors:
            detail = '\n'.join(
                str(row.get('error') or row) for row in errors if row
            )
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Some jobs could not be deleted.',
                det_msg=detail,
                show=True,
            )
        return deleted

    def clear_jobs(
        self,
        *,
        finished: bool = False,
        failed: bool = False,
        stopped: bool = False,
        inactive: bool = False,
    ) -> list[str]:
        code, stdout, stderr = run_ao3kit(
            build_job_clear_argv(
                finished=finished,
                failed=failed,
                stopped=stopped,
                inactive=inactive,
                jobs_dir=self._jobs_dir_arg(),
            )
        )
        payload = parse_job_status_json(stdout) or {}
        deleted = [str(item) for item in (payload.get('deleted') or [])]
        for job_id in deleted:
            self.forget_job(job_id)
        if code != 0 and not deleted:
            error_dialog(
                self.gui,
                'Fanfic Organizer',
                'Could not clear jobs.',
                det_msg=(stderr or stdout or f'exit {code}').strip(),
                show=True,
            )
        return deleted

    def show_list(self) -> None:
        from calibre_plugins.fanfic_organizer.job_ui import JobsListDialog

        if self._list_dialog is not None:
            try:
                self._list_dialog.raise_()
                self._list_dialog.activateWindow()
                self._list_dialog.reload()
                return
            except RuntimeError:
                self._list_dialog = None
        dialog = JobsListDialog(self.gui, self)
        dialog.finished.connect(lambda *_a: setattr(self, '_list_dialog', None))
        self._list_dialog = dialog
        dialog.show()

    def _paths_for(self, job_id: str) -> tuple[Path | None, Path | None, str]:
        if job_id == 'warm':
            project = self._project()
            if project is None:
                return None, None, 'Background tag cache'
            from calibre_plugins.fanfic_organizer.tag_warm import warm_log_path, warm_status_path

            return (
                warm_log_path(project),
                warm_status_path(project),
                'Background tag cache',
            )
        root = self.jobs_dir()
        if root is None:
            return None, None, job_id
        paths = job_paths(root / job_id)
        spec = read_json(paths['spec']) or {}
        title = str(spec.get('title') or 'Fanfic Organizer — Job')
        return paths['log'], paths['status'], title

    def tick(self) -> None:
        self._drain_graph_commands()
        root = self.jobs_dir()
        if root is None or not root.is_dir():
            return
        for child in list(root.iterdir()):
            if child.is_dir():
                self._tick_job(child)

    def _tick_job(self, job_dir: Path) -> None:
        spec = read_json(job_dir / 'spec.json') or {}
        status = read_json(job_dir / 'status.json') or {}
        job_id = str(status.get('id') or spec.get('id') or job_dir.name)
        plugin = spec.get('plugin') or {}
        if status.get('running'):
            if plugin.get('incremental_import'):
                self._poll_import(job_id, plugin)
            elif plugin.get('incremental_epubs'):
                self._poll_epubs(job_id, plugin)
            return
        if job_was_notified(status) or job_id in self._ingesting:
            return
        ingest = str(status.get('ingest') or 'none')
        if ingest == 'pending':
            self._ingesting.add(job_id)
            try:
                self._run_ingest(job_id, spec, plugin, status)
            except Exception:
                detail = traceback.format_exc()
                self._mark_ingest(job_dir, 'failed', error=detail)
                self._notify(
                    job_id,
                    'Job finished but Calibre update failed.',
                    ok=False,
                    detail=detail,
                )
            finally:
                self._ingesting.discard(job_id)
            return
        if ingest == 'cancelled':
            if plugin.get('incremental_import'):
                self._poll_import(job_id, plugin)
            if str(spec.get('kind') or '') != 'graph':
                self._notify(job_id, status.get('message') or 'Stopped.', ok=True)
            else:
                self._mark_notified(job_dir)
            return
        if ingest == 'skipped' or status.get('exit_code') not in (None, 0):
            if plugin.get('action'):
                if plugin.get('incremental_import'):
                    self._poll_import(job_id, plugin)
                self._finish_failed(job_id, status)
            else:
                self._mark_notified(job_dir)
            return
        finished = bool(status.get('finished_at')) or status.get('exit_code') is not None
        if ingest == 'done' or (ingest == 'none' and finished):
            summary = (
                first_line(status.get('result'), 200)
                or first_line(status.get('message'), 200)
                or 'Finished.'
            )
            self._notify(job_id, summary, ok=True)

    def _poll_epubs(self, job_id: str, plugin: dict[str, Any]) -> None:
        items_path = plugin.get('items_json')
        jsonl = plugin.get('jsonl')
        bundle = plugin.get('bundle_root')
        if not items_path or not jsonl or not bundle:
            return
        payload = read_json(Path(items_path)) or {}
        ready = payload.get('ready') or []
        seen = self._epub_seen.setdefault(job_id, set())
        try:
            downloaded = load_jsonl_records(jsonl)
        except (OSError, ValueError):
            return
        db = self.gui.current_db
        for item in pending_epub_attachments(ready, downloaded, seen):
            if resolve_epub_path(item.get('record') or {}, bundle) is None:
                continue
            book_id = item.get('book_id')
            if book_id is None or book_has_epub(db, book_id):
                seen.add(book_id)
                continue
            outcomes = attach_downloaded_epubs(db, [item], bundle_root=bundle)
            seen.add(book_id)
            ids = [
                row['book_id']
                for row in outcomes
                if row.get('epub') and row.get('book_id')
            ]
            if ids:
                refresh_library_ui(self.gui, ids)
            dialog = self._dialogs.get(job_id)
            title = item.get('title') or (item.get('record') or {}).get('title') or book_id
            if dialog is not None:
                try:
                    dialog._append(f'Added EPUB to {title}.')
                except RuntimeError:
                    pass

    def _poll_import(self, job_id: str, plugin: dict[str, Any]) -> None:
        jsonl = plugin.get('results_jsonl') or plugin.get('jsonl')
        bundle = plugin.get('bundle_root') or None
        if not jsonl:
            return
        try:
            records = load_jsonl_records(jsonl)
        except (OSError, ValueError):
            return
        records = [record for record in records if not scrape_record_failed(record)]
        imported = self._import_seen.setdefault(job_id, {})
        new_records, epub_records = pending_incremental_imports(
            records, imported, work_id_of=canonical_work_id
        )
        if not new_records and not epub_records:
            return
        try:
            db = apply_layout_columns(self.gui)
            update_existing = bool(plugin.get('update_existing', True))
            skip_existing_epub = bool(plugin.get('skip_existing_epub', True))
            catalog = iter_identifier_maps(db)
            book_ids: list[Any] = []
            added_count = 0
            dialog = self._dialogs.get(job_id)
            for record in new_records:
                work_id = canonical_work_id(record)
                if not work_id:
                    continue
                outcome = import_record(
                    db,
                    record,
                    update_existing=update_existing,
                    bundle_root=bundle,
                    skip_existing_epub=skip_existing_epub,
                    catalog=catalog,
                )
                book_id = outcome.get('book_id')
                action = outcome.get('action')
                imported[work_id] = {
                    'book_id': book_id,
                    'has_epub': bool(outcome.get('epub')) or action == 'skipped',
                }
                if book_id is not None:
                    book_ids.append(book_id)
                if action == 'added':
                    added_count += 1
                title = record.get('title') or work_id
                if dialog is not None and action in ('added', 'updated'):
                    try:
                        verb = 'Added' if action == 'added' else 'Updated'
                        dialog._append(f'{verb} {title}.')
                        if outcome.get('epub'):
                            dialog._append(f'Added EPUB to {title}.')
                    except RuntimeError:
                        dialog = None
            for record in epub_records:
                work_id = canonical_work_id(record)
                state = imported.get(work_id) or {}
                book_id = state.get('book_id')
                if book_id is None or not bundle:
                    continue
                if resolve_epub_path(record, bundle) is None:
                    continue
                if book_has_epub(db, book_id):
                    state['has_epub'] = True
                    continue
                item = {
                    'book_id': book_id,
                    'record': record,
                    'title': record.get('title'),
                }
                outcomes = attach_downloaded_epubs(
                    db, [item], bundle_root=bundle
                )
                if any(row.get('epub') for row in outcomes):
                    state['has_epub'] = True
                    book_ids.append(book_id)
                    title = record.get('title') or work_id
                    if dialog is not None:
                        try:
                            dialog._append(f'Added EPUB to {title}.')
                        except RuntimeError:
                            dialog = None
            if book_ids:
                refresh_library_ui(self.gui, book_ids, added_count=added_count)
            if new_records:
                project = self._project()
                if project is not None:
                    from calibre_plugins.fanfic_organizer.graph_live import (
                        graph_jsonl_path,
                        upsert_graph_jsonl,
                    )

                    upsert_graph_jsonl(graph_jsonl_path(project), new_records)
        except Exception:
            return

    def _run_ingest(
        self,
        job_id: str,
        spec: dict[str, Any],
        plugin: dict[str, Any],
        status: dict[str, Any],
    ) -> None:
        raw_actions = plugin.get('actions')
        if isinstance(raw_actions, list) and raw_actions:
            actions = [str(item or '').strip() or 'none' for item in raw_actions]
        else:
            actions = [str(plugin.get('action') or 'none')]
        root = self.jobs_dir()
        job_dir = root / job_id if root is not None else None
        dialog = self._dialogs.get(job_id)
        if all(action in ('', 'none') for action in actions):
            if job_dir is not None:
                self._mark_ingest(job_dir, 'none')
            self._notify(job_id, status.get('message') or 'Finished.', ok=True)
            return
        if dialog is not None:
            try:
                dialog.mark_working('Writing into Calibre library…')
            except RuntimeError:
                pass
        summaries: list[str] = []
        details: list[str] = []
        for action in actions:
            if action in ('', 'none'):
                continue
            summary, detail = self._ingest_action(job_id, plugin, action)
            if summary:
                summaries.append(summary)
            if detail:
                details.append(detail)
        summary = ' '.join(summaries) if summaries else 'Finished.'
        detail = '\n\n'.join(details)
        if job_dir is not None:
            self._mark_ingest(job_dir, 'done')
        cleanup = plugin.get('cleanup_dir')
        if cleanup:
            import shutil

            shutil.rmtree(cleanup, ignore_errors=True)
        ok = True
        exit_code = status.get('exit_code')
        if exit_code not in (None, 0) and not any(
            token in summary.lower()
            for token in ('imported', 'updated', 'added', 'simplified', 'recomputed')
        ):
            ok = False
        elif exit_code not in (None, 0):
            summary = f'Finished with errors. {summary}'
        self._notify(job_id, summary, ok=ok, detail=detail)

    def _ingest_action(
        self, job_id: str, plugin: dict[str, Any], action: str
    ) -> tuple[str, str]:
        if action == 'import_records':
            return self._ingest_import(plugin)
        if action == 'attach_epubs':
            return self._ingest_epubs(job_id, plugin)
        if action == 'apply_cleaned':
            return self._ingest_apply(plugin, collections=False)
        if action == 'apply_collections':
            return self._ingest_apply(plugin, collections=True)
        if action == 'apply_series':
            return self._ingest_series(plugin)
        if action == 'apply_covers':
            return self._ingest_covers(plugin)
        if action == 'resolve_identify':
            return self._ingest_identify(plugin)
        return f'Unknown ingest action {action!r}.', ''

    def _import_records_for_plugin(
        self, plugin: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        jsonl = plugin.get('jsonl')
        if not jsonl or not Path(jsonl).is_file():
            return [], []
        try:
            records = load_jsonl_records(jsonl)
        except (OSError, ValueError):
            return [], []
        failed = [record for record in records if scrape_record_failed(record)]
        records = [record for record in records if not scrape_record_failed(record)]
        items_path = plugin.get('items_json')
        if items_path and Path(items_path).is_file():
            payload = read_json(Path(items_path)) or {}
            ready = payload.get('ready') or []
            if ready:
                items = merge_ready_with_jsonl(
                    ready, records, work_id_of=canonical_work_id
                )
                records = [
                    item['record']
                    for item in items
                    if item.get('record') and not scrape_record_failed(item['record'])
                ]
        return records, failed

    def _ingest_import(self, plugin: dict[str, Any]) -> tuple[str, str]:
        records, failed = self._import_records_for_plugin(plugin)
        if not records and not failed:
            return 'No works matched that search and those filters.', ''
        if not records:
            lines = [
                f"{item.get('title') or item.get('work_id')}: {item.get('scrape_error')}"
                for item in failed[:20]
            ]
            return (
                f'Could not fetch metadata for {len(failed)} book(s) from AO3.',
                '\n'.join(lines),
            )
        project = self._project()
        if project is not None:
            from calibre_plugins.fanfic_organizer.graph_live import (
                graph_jsonl_path,
                upsert_graph_jsonl,
            )

            upsert_graph_jsonl(graph_jsonl_path(project), records)
        payload = {
            'records': records,
            'bundle_root': plugin.get('bundle_root') or None,
            'enrich_note': '',
        }
        summary, remap_text, book_ids = write_import_payload(
            self.gui,
            payload,
            update_existing=bool(plugin.get('update_existing', True)),
            skip_existing_epub=bool(plugin.get('skip_existing_epub', True)),
        )
        refresh_library_ui(self.gui, book_ids)
        if failed:
            lines = [
                f"{item.get('title') or item.get('work_id')}: {item.get('scrape_error')}"
                for item in failed[:20]
            ]
            extra = f'; {len(failed)} book(s) could not be fetched from AO3'
            summary = summary.rstrip('.') + extra + '.'
            if remap_text:
                remap_text = remap_text + '\n\n' + '\n'.join(lines)
            else:
                remap_text = '\n'.join(lines)
        skipped = plugin.get('skipped') or []
        if skipped:
            skip_lines = [
                f"{item.get('title') or item.get('book_id')}: {item.get('reason')}"
                for item in skipped[:20]
            ]
            summary = summary.rstrip('.') + f'; skipped {len(skipped)} earlier.'
            if remap_text:
                remap_text = remap_text + '\n\n' + '\n'.join(skip_lines)
            else:
                remap_text = '\n'.join(skip_lines)
        return summary, remap_text

    def _ingest_epubs(self, job_id: str, plugin: dict[str, Any]) -> tuple[str, str]:
        payload = read_json(Path(plugin.get('items_json') or '')) or {}
        ready = payload.get('ready') or []
        skipped = payload.get('skipped') or []
        jsonl = plugin.get('results_jsonl') or plugin.get('jsonl')
        bundle = plugin.get('bundle_root')
        downloaded: list[dict[str, Any]] = []
        if jsonl and Path(jsonl).is_file():
            try:
                downloaded = load_jsonl_records(jsonl)
            except (OSError, ValueError):
                downloaded = []
        items = merge_download_manifest(ready, downloaded) if ready else []
        seen = self._epub_seen.get(job_id) or set()
        db = self.gui.current_db
        outcomes: list[dict[str, Any]] = []
        for item in items:
            book_id = item.get('book_id')
            if book_id in seen:
                continue
            record = item.get('record') or {}
            if record.get('epub_file') and bundle:
                if book_has_epub(db, book_id):
                    seen.add(book_id)
                    continue
                outcomes.extend(
                    attach_downloaded_epubs(db, [item], bundle_root=bundle)
                )
                seen.add(book_id)
        summary = summarize_epub_download(outcomes, skipped)
        ids = [row['book_id'] for row in outcomes if row.get('book_id')]
        if ids:
            refresh_library_ui(self.gui, ids)
        return summary, ''

    def _ingest_apply(self, plugin: dict[str, Any], *, collections: bool) -> tuple[str, str]:
        payload = read_json(Path(plugin.get('items_json') or '')) or {}
        ready = payload.get('ready') or []
        skipped = payload.get('skipped') or []
        jsonl = plugin.get('jsonl')
        records: list[dict[str, Any]] = []
        if jsonl and Path(jsonl).is_file():
            records = load_jsonl_records(jsonl)
        items = merge_ready_with_jsonl(ready, records, work_id_of=canonical_work_id)
        db = apply_layout_columns(self.gui)
        if collections:
            outcomes = apply_collections_records(db, items)
            updated = sum(1 for item in outcomes if item.get('action') == 'updated')
            summary = (
                f'Recomputed collections on {updated} of {len(outcomes)} '
                f'{_book_noun(len(outcomes))}'
            )
            if skipped:
                summary += f'; skipped {len(skipped)}'
            summary += '.'
            records_out = [item['record'] for item in items]
            summary, remap = _finish_with_collections(summary, records_out)
        else:
            outcomes = apply_cleaned_records(db, items)
            summary = f'Simplified tags for {len(outcomes)} book(s)'
            if skipped:
                summary += f'; skipped {len(skipped)} without an AO3 URL / work id'
            summary += '.'
            records_out = [item['record'] for item in items]
            summary, remap = _finish_with_remaps(summary, records_out)
        refresh_library_ui(
            self.gui,
            [item['book_id'] for item in outcomes if item.get('book_id') is not None],
        )
        return summary, remap

    def _ingest_series(self, plugin: dict[str, Any]) -> tuple[str, str]:
        payload = read_json(Path(plugin.get('items_json') or '')) or {}
        ready = payload.get('ready') or []
        skipped = payload.get('skipped') or []
        jsonl = plugin.get('jsonl')
        records: list[dict[str, Any]] = []
        if jsonl and Path(jsonl).is_file():
            records = load_jsonl_records(jsonl)
        items = merge_ready_with_jsonl(ready, records, work_id_of=canonical_work_id)
        outcomes = apply_series_records(self.gui.current_db, items)
        filled = [item for item in outcomes if item.get('in_series')]
        summary = f'Filled Series on {len(filled)} book(s)'
        extras = []
        not_in = len(outcomes) - len(filled)
        if not_in:
            extras.append(f'{not_in} not in an AO3 series')
        if skipped:
            extras.append(f'skipped {len(skipped)} without an AO3 URL / work id')
        if extras:
            summary += ' (' + '; '.join(extras) + ')'
        summary += '.'
        detail_lines = []
        for item in filled:
            name = item.get('series') or ''
            index = item.get('series_index')
            part = f' part {int(index)}' if index is not None else ''
            detail_lines.append(
                f"{item.get('title') or item.get('book_id')}: {name}{part}"
            )
        refresh_library_ui(
            self.gui,
            [item['book_id'] for item in outcomes if item.get('book_id') is not None],
        )
        return summary, '\n'.join(detail_lines)

    def _ingest_covers(self, plugin: dict[str, Any]) -> tuple[str, str]:
        payload = read_json(Path(plugin.get('items_json') or '')) or {}
        ready = payload.get('ready') or []
        skipped = payload.get('skipped') or []
        items = ready
        outcomes = apply_cover_records(
            self.gui.current_db,
            items,
            bundle_root=plugin.get('bundle_root'),
            png_dir=plugin.get('png_dir'),
            set_calibre_cover=bool(plugin.get('set_calibre_cover', True)),
        )
        updated = [item for item in outcomes if item.get('action') == 'updated']
        covers = sum(1 for item in updated if item.get('cover'))
        epubs = sum(1 for item in updated if item.get('epub'))
        summary = f'Generated covers for {len(updated)} book(s)'
        bits = []
        if covers:
            bits.append(f'{covers} Calibre cover')
        if epubs:
            bits.append(f'{epubs} EPUB')
        if bits:
            summary += ' (' + ', '.join(bits) + ')'
        if skipped:
            summary += f'; skipped {len(skipped)}'
        summary += '.'
        if covers:
            summary += (
                ' The book list does not show covers — open the book, or use '
                'Edit metadata / Cover browser / Grid view.'
            )
        detail_lines = [
            f"{item.get('title') or item.get('book_id')}"
            for item in updated
        ]
        refresh_library_ui(
            self.gui,
            [item['book_id'] for item in outcomes if item.get('book_id') is not None],
        )
        return summary, '\n'.join(detail_lines)

    def _ingest_identify(self, plugin: dict[str, Any]) -> tuple[str, str]:
        from calibre_plugins.fanfic_organizer.dialogs import IdentifyWorksDialog
        from calibre_plugins.fanfic_organizer.scrape_run import merge_plugin_settings
        from calibre_plugins.fanfic_organizer.prefs import plugin_runtime_settings

        payload = read_json(Path(plugin.get('items_json') or '')) or {}
        ready = payload.get('ready') or []
        skipped = list(payload.get('skipped') or [])
        jsonl = plugin.get('jsonl')
        records = load_identify_jsonl(jsonl) if jsonl else []
        if not records:
            records = [item.get('record') or {} for item in ready]
        identified, ambiguous, failed = split_identify_records(records)
        if ambiguous:
            titles = {
                item.get('book_id'): item.get('title')
                for item in ready
                if item.get('book_id') is not None
            }
            dialog = IdentifyWorksDialog(self.gui, ambiguous, titles=titles)
            if dialog.exec_():
                picked = apply_identify_choices(
                    identified + ambiguous, dialog.choices()
                )
                kept = {
                    str(item.get('book_id') or item.get('calibre_book_id') or '')
                    for item in picked
                }
                for item in ambiguous:
                    book_id = str(item.get('book_id') or item.get('calibre_book_id') or '')
                    if book_id and book_id not in kept:
                        skipped.append(
                            {
                                'book_id': item.get('book_id'),
                                'title': item.get('title'),
                                'reason': 'skipped in the picker',
                            }
                        )
                identified = picked
            else:
                skipped.extend(
                    {
                        'book_id': item.get('book_id'),
                        'title': item.get('title'),
                        'reason': 'skipped in the picker',
                    }
                    for item in ambiguous
                )
                ambiguous = []
        skipped.extend(
            {
                'book_id': item.get('book_id'),
                'title': item.get('title'),
                'reason': item.get('reason') or item.get('status') or 'not identified',
            }
            for item in failed
        )
        fill_ready = merge_identify_ready(ready, identified)
        db = apply_layout_columns(self.gui)
        stamped = 0
        for item in fill_ready:
            record = item.get('record') or {}
            work_id = str(record.get('work_id') or '').strip()
            book_id = item.get('book_id')
            if book_id is None or not work_id:
                continue
            if stamp_ao3_identifiers(
                db, int(book_id), work_id, str(record.get('url') or '')
            ):
                stamped += 1
        if stamped:
            refresh_library_ui(
                self.gui,
                [item['book_id'] for item in fill_ready if item.get('book_id') is not None],
            )
        if not fill_ready:
            n_fail = len(skipped)
            noun = 'book' if n_fail == 1 else 'books'
            return (
                f'Could not identify any of the selected {noun}.',
                '\n'.join(
                    f'{item.get("title")}: {item.get("reason")}'
                    for item in skipped[:20]
                ),
            )
        job_dir = self.prepare_job_dir('fill')
        if job_dir is None:
            return (
                f'Identified {len(fill_ready)} book(s) but could not start the fill job.',
                '',
            )
        epub_dir = Path(job_dir) / 'work' / 'bundle' / 'epubs'
        epub_dir.mkdir(parents=True, exist_ok=True)
        for item in fill_ready:
            record = item.get('record') or {}
            work_id = str(record.get('work_id') or '').strip()
            book_id = item.get('book_id')
            if work_id and book_id is not None:
                copy_book_epub(db, int(book_id), epub_dir / f'{work_id}.epub')
        options = merge_plugin_settings(
            dict(plugin.get('fill_options') or {}),
            plugin_runtime_settings(),
        )
        options['update_existing'] = True
        plan_fill_from_ao3(fill_ready, skipped, job_dir, options)
        started = self.start_prepared(job_dir, attach=True)
        n = len(fill_ready)
        noun = 'book' if n == 1 else 'books'
        summary = f'Identified {n} {noun}'
        if skipped:
            summary += f'; skipped {len(skipped)}'
        if started:
            summary += '. Filling metadata from AO3…'
        else:
            summary += ' but the fill job did not start.'
        detail_lines = [
            f"{item.get('title') or item.get('book_id')}: "
            f"AO3 {(item.get('record') or {}).get('work_id')}"
            for item in fill_ready
        ]
        return summary, '\n'.join(detail_lines)

    def _mark_ingest(self, job_dir: Path, ingest: str, error: str | None = None) -> None:
        fields = {'ingest': ingest}
        if error:
            fields['ingest_error'] = error
        self._update_status(job_dir, **fields)

    def _mark_notified(self, job_dir: Path) -> None:
        self._update_status(job_dir, notified=True)

    def _update_status(self, job_dir: Path, **fields: Any) -> None:
        if not job_dir.is_dir():
            return
        status = read_json(job_dir / 'status.json') or {}
        status.update(fields)
        write_json(job_dir / 'status.json', status)

    def _finish_failed(self, job_id: str, status: dict[str, Any]) -> None:
        root = self.jobs_dir()
        if root is not None:
            self._mark_ingest(root / job_id, 'skipped')
        message = str(status.get('message') or 'Job failed.')
        self._notify(job_id, message, ok=False, detail=message)

    def _live_log(self, job_id: str) -> JobLogDialog | None:
        dialog = self._dialogs.get(job_id)
        if dialog is not None:
            try:
                dialog.windowTitle()
                return dialog
            except RuntimeError:
                self._dialogs.pop(job_id, None)
        try:
            from PyQt5.Qt import QApplication
        except ImportError:
            from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return None
        for widget in app.topLevelWidgets():
            if (
                isinstance(widget, JobLogDialog)
                and getattr(widget, 'job_id', None) == job_id
            ):
                self._dialogs[job_id] = widget
                return widget
        return None

    def _notify(self, job_id: str, summary: str, *, ok: bool, detail: str = '') -> None:
        root = self.jobs_dir()
        job_dir = root / job_id if root is not None else None
        result = first_line(summary, 200)
        if job_dir is not None:
            fields = {'notified': True}
            if result:
                fields['result'] = result
            self._update_status(job_dir, **fields)
        dialog = self._live_log(job_id)
        if dialog is not None:
            try:
                dialog.mark_finished(summary, ok=ok, detail=detail)
                dialog.raise_()
                dialog.activateWindow()
                return
            except RuntimeError:
                self._dialogs.pop(job_id, None)
        status = {}
        if job_dir is not None:
            status = read_json(job_dir / 'status.json') or {}
        status.setdefault('id', job_id)
        popup = JobNotifyDialog(
            self.gui,
            summary=summary,
            detail=detail,
            ok=ok,
            retryable=job_is_retryable(status),
        )
        popup.exec_()
        if popup.should_retry:
            self.retry(job_id)
