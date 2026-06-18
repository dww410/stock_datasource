"""TuShare sync run-state tracking for resume capability."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from stock_datasource.config.settings import settings
from stock_datasource.utils.logger import logger


@dataclass
class PluginRunResult:
    """Result of a single plugin run."""
    plugin_name: str
    operation: str
    params: dict[str, Any]
    status: str  # success, failed, skipped, pending
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    records_processed: int = 0
    error_message: Optional[str] = None
    priority: int = 0


@dataclass
class SyncRun:
    """A complete sync run with all execution state."""
    run_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str = "running"  # running, completed, failed, partial
    years: int = 3
    plugin_filter: list[str] = field(default_factory=list)
    only_empty: bool = False
    max_plugins: Optional[int] = None
    actions: list[PluginRunResult] = field(default_factory=list)
    total_actions: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    error_message: Optional[str] = None

    @property
    def completed_count(self) -> int:
        return self.successful + self.failed + self.skipped

    @property
    def progress_pct(self) -> float:
        if self.total_actions == 0:
            return 0.0
        return self.completed_count / self.total_actions * 100


class SyncStateManager:
    """Manages sync run state persistence and resumption."""

    def __init__(self, state_dir: Optional[Path] = None):
        self.logger = logger.bind(component="SyncStateManager")
        self.state_dir = state_dir or settings.DATA_DIR / "tushare_sync_states"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def create_run(
        self,
        years: int = 3,
        plugin_filter: Optional[list[str]] = None,
        only_empty: bool = False,
        max_plugins: Optional[int] = None,
    ) -> SyncRun:
        """Create a new sync run."""
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]

        run = SyncRun(
            run_id=run_id,
            started_at=datetime.now(),
            years=years,
            plugin_filter=plugin_filter or [],
            only_empty=only_empty,
            max_plugins=max_plugins,
        )

        self._save_run(run)
        self.logger.info(f"Created new sync run: {run_id}")
        return run

    def add_action(
        self,
        run: SyncRun,
        plugin_name: str,
        operation: str,
        params: dict[str, Any],
        priority: int = 0,
    ) -> PluginRunResult:
        """Add a pending action to the run."""
        action = PluginRunResult(
            plugin_name=plugin_name,
            operation=operation,
            params=params,
            status="pending",
            priority=priority,
        )
        run.actions.append(action)
        run.total_actions = len(run.actions)
        self._save_run(run)
        return action

    def mark_started(self, run: SyncRun, plugin_name: str) -> Optional[PluginRunResult]:
        """Mark a plugin action as started."""
        action = self._find_action(run, plugin_name)
        if action:
            action.status = "running"
            action.started_at = datetime.now()
            self._save_run(run)
        return action

    def mark_success(
        self,
        run: SyncRun,
        plugin_name: str,
        records_processed: int = 0,
    ) -> Optional[PluginRunResult]:
        """Mark a plugin action as successfully completed."""
        action = self._find_action(run, plugin_name)
        if action:
            action.status = "success"
            action.completed_at = datetime.now()
            action.records_processed = records_processed
            run.successful += 1
            self._save_run(run)
        return action

    def mark_failed(
        self,
        run: SyncRun,
        plugin_name: str,
        error_message: str,
    ) -> Optional[PluginRunResult]:
        """Mark a plugin action as failed."""
        action = self._find_action(run, plugin_name)
        if action:
            action.status = "failed"
            action.completed_at = datetime.now()
            action.error_message = error_message
            run.failed += 1
            self._save_run(run)
        return action

    def mark_skipped(
        self,
        run: SyncRun,
        plugin_name: str,
        reason: str = "already completed on resume",
    ) -> Optional[PluginRunResult]:
        """Mark a plugin action as skipped (already done in previous run)."""
        action = self._find_action(run, plugin_name)
        if action:
            action.status = "skipped"
            action.error_message = reason
            run.skipped += 1
            self._save_run(run)
        return action

    def complete_run(self, run: SyncRun, status: str = "completed") -> None:
        """Mark the entire run as completed."""
        run.completed_at = datetime.now()
        run.status = status

        # Recalculate counts
        run.successful = sum(1 for a in run.actions if a.status == "success")
        run.failed = sum(1 for a in run.actions if a.status == "failed")
        run.skipped = sum(1 for a in run.actions if a.status == "skipped")

        self._save_run(run)
        self.logger.info(f"Sync run {run.run_id} {status}: "
                        f"{run.successful} ok, {run.failed} fail, {run.skipped} skip")

    def get_run(self, run_id: str) -> Optional[SyncRun]:
        """Load a run by ID."""
        path = self.state_dir / f"{run_id}.json"
        if not path.exists():
            return None

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            return self._dict_to_run(data)
        except Exception as e:
            self.logger.error(f"Failed to load run {run_id}: {e}")
            return None

    def list_runs(self, limit: int = 10) -> list[SyncRun]:
        """List recent runs."""
        runs = []
        for path in sorted(self.state_dir.glob("*.json"), reverse=True):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    runs.append(self._dict_to_run(data))
            except Exception:
                continue

            if len(runs) >= limit:
                break

        return runs

    def get_pending_actions(self, run: SyncRun) -> list[PluginRunResult]:
        """Get list of actions that haven't completed yet (for resume)."""
        return [
            a for a in run.actions
            if a.status in ("pending", "running", "failed")
        ]

    def _find_action(self, run: SyncRun, plugin_name: str) -> Optional[PluginRunResult]:
        """Find an action by plugin name."""
        for action in run.actions:
            if action.plugin_name == plugin_name:
                return action
        return None

    def _save_run(self, run: SyncRun) -> None:
        """Save run state to disk."""
        path = self.state_dir / f"{run.run_id}.json"
        data = self._run_to_dict(run)

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"Failed to save run {run.run_id}: {e}")

    def _run_to_dict(self, run: SyncRun) -> dict[str, Any]:
        """Convert SyncRun to serializable dict."""
        return {
            "run_id": run.run_id,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "status": run.status,
            "years": run.years,
            "plugin_filter": run.plugin_filter,
            "only_empty": run.only_empty,
            "max_plugins": run.max_plugins,
            "total_actions": run.total_actions,
            "successful": run.successful,
            "failed": run.failed,
            "skipped": run.skipped,
            "error_message": run.error_message,
            "actions": [
                {
                    "plugin_name": a.plugin_name,
                    "operation": a.operation,
                    "params": a.params,
                    "status": a.status,
                    "started_at": a.started_at.isoformat() if a.started_at else None,
                    "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                    "records_processed": a.records_processed,
                    "error_message": a.error_message,
                    "priority": a.priority,
                }
                for a in run.actions
            ],
        }

    def _dict_to_run(self, data: dict[str, Any]) -> SyncRun:
        """Convert dict back to SyncRun object."""
        run = SyncRun(
            run_id=data["run_id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            status=data["status"],
            years=data.get("years", 3),
            plugin_filter=data.get("plugin_filter", []),
            only_empty=data.get("only_empty", False),
            max_plugins=data.get("max_plugins"),
            total_actions=data.get("total_actions", 0),
            successful=data.get("successful", 0),
            failed=data.get("failed", 0),
            skipped=data.get("skipped", 0),
            error_message=data.get("error_message"),
        )

        run.actions = [
            PluginRunResult(
                plugin_name=a["plugin_name"],
                operation=a["operation"],
                params=a.get("params", {}),
                status=a["status"],
                started_at=datetime.fromisoformat(a["started_at"]) if a.get("started_at") else None,
                completed_at=datetime.fromisoformat(a["completed_at"]) if a.get("completed_at") else None,
                records_processed=a.get("records_processed", 0),
                error_message=a.get("error_message"),
                priority=a.get("priority", 0),
            )
            for a in data.get("actions", [])
        ]

        return run


# Global manager instance
_state_manager: Optional[SyncStateManager] = None


def get_sync_state_manager() -> SyncStateManager:
    """Get or create the global sync state manager."""
    global _state_manager
    if _state_manager is None:
        _state_manager = SyncStateManager()
    return _state_manager
