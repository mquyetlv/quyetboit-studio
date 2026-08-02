from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from .core.models import PipelineError, ProgressEvent
from .models import STEP_TITLES, ProjectModel, StepStatus


class PipelineService(ABC):
    @abstractmethod
    def message_for(self, step: int, progress: int) -> str: ...


class MockPipelineService(PipelineService):
    def message_for(self, step: int, progress: int) -> str:
        verbs = ("Phân tích nguồn", "Chia đoạn audio", "Tạo phụ đề", "Dịch nội dung",
                 "Tổng hợp giọng", "Đồng bộ âm thanh", "Áp dụng hiệu ứng", "Render video")
        return f"{verbs[step]} — {progress}% (mô phỏng)"


class PipelineWorker(QObject):
    progress = Signal(int, int, str, str)
    succeeded = Signal(int, object)
    failed = Signal(int, object)
    finished = Signal()

    def __init__(self, service: object, step: int) -> None:
        super().__init__()
        self.service = service
        self.step = step

    @Slot()
    def run(self) -> None:
        try:
            result = self.service.run_step(self.step, self._progress)
            self.succeeded.emit(self.step, result)
        except Exception as exc:  # noqa: BLE001 - worker must forward all task failures to the UI
            self.failed.emit(self.step, exc)
        finally:
            self.finished.emit()

    def _progress(self, event: ProgressEvent) -> None:
        self.progress.emit(event.step, round(max(0.0, min(1.0, event.fraction)) * 100), event.message, event.state)


class WorkflowController(QObject):
    step_status_changed = Signal(int, str)
    step_progress_changed = Signal(int, int)
    overall_progress_changed = Signal(int)
    log_emitted = Signal(str, str)
    workflow_completed = Signal()
    dirty_changed = Signal(bool)

    def __init__(self, project: ProjectModel, service: PipelineService | None = None) -> None:
        super().__init__()
        self.project = project
        self.service = service or MockPipelineService()
        self.timer = QTimer(self)
        self.timer.setInterval(90)
        self.timer.timeout.connect(self._tick)
        self.active_step: int | None = None
        self.progress = 0
        self.run_all_mode = False
        self.is_paused = False
        self.thread: QThread | None = None
        self.worker: PipelineWorker | None = None
        self.pending_step: int | None = None
        self.paused_step: int | None = None

    @property
    def is_real(self) -> bool:
        return hasattr(self.service, "run_step")

    def can_run(self, step: int) -> tuple[bool, str]:
        if not 0 <= step < 8:
            return False, "Bước không hợp lệ."
        if step > 0 and self.project.step_statuses[step - 1] != StepStatus.COMPLETED.value:
            return False, f"Hãy hoàn thành bước {step} trước."
        if self.active_step is not None or self.thread is not None:
            return False, "Một tác vụ khác đang chạy."
        return True, ""

    def run_step(self, step: int) -> bool:
        allowed, reason = self.can_run(step)
        if not allowed:
            self.log_emitted.emit("WARNING", reason)
            return False
        self.active_step = step
        self.progress = 0
        self.is_paused = False
        if self.is_real and hasattr(self.service, "cancel") and self.service.cancel.is_cancelled:
            from .core.models import CancellationToken
            self.service.cancel = CancellationToken()
        self._set_status(step, StepStatus.RUNNING)
        if self.is_real:
            self.overall_progress_changed.emit(self._weighted_progress(step, 0))
        self.log_emitted.emit("INFO", f"Bắt đầu bước {step + 1}: {STEP_TITLES[step]}.")
        if self.is_real:
            self._start_worker(step)
        else:
            self.timer.start()
        return True

    def _start_worker(self, step: int) -> None:
        self.thread = QThread(self)
        self.worker = PipelineWorker(self.service, step)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._worker_progress)
        self.worker.succeeded.connect(self._worker_succeeded)
        self.worker.failed.connect(self._worker_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    @Slot(int, int, str, str)
    def _worker_progress(self, step: int, value: int, message: str, state: str) -> None:
        self.progress = value
        self.step_progress_changed.emit(step, value)
        self.overall_progress_changed.emit(self._weighted_progress(step, value))
        if message:
            self.log_emitted.emit("INFO", message)

    @Slot(int, object)
    def _worker_succeeded(self, step: int, result: object) -> None:
        self.progress = 100
        self.step_progress_changed.emit(step, 100)
        self.overall_progress_changed.emit(self._weighted_progress(step, 100))
        self._set_status(step, StepStatus.COMPLETED)
        self.log_emitted.emit("SUCCESS", f"Hoàn thành bước {step + 1}: {STEP_TITLES[step]}.")
        self.active_step = None
        self.dirty_changed.emit(True)
        if step < 7:
            self._set_status(step + 1, StepStatus.READY)
        if self.run_all_mode and step < 7:
            self.pending_step = step + 1
        elif step == 7:
            self.run_all_mode = False
            self.workflow_completed.emit()

    @Slot(int, object)
    def _worker_failed(self, step: int, error: BaseException) -> None:
        self.active_step = None
        self.run_all_mode = False
        if self.is_paused:
            self._set_status(step, StepStatus.PAUSED)
            return
        if isinstance(error, PipelineError) and error.error_code == "cancelled":
            self._set_status(step, StepStatus.CANCELLED)
            level = "WARNING"
        else:
            self._set_status(step, StepStatus.FAILED)
            level = "ERROR"
        message = error.user_message if isinstance(error, PipelineError) else str(error)
        self.log_emitted.emit(level, message)

    @Slot()
    def _thread_finished(self) -> None:
        if self.thread:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        if self.pending_step is not None:
            step, self.pending_step = self.pending_step, None
            QTimer.singleShot(0, lambda: self.run_step(step))

    def run_all(self) -> bool:
        if self.active_step is not None:
            return False
        self.run_all_mode = True
        first = next((i for i, status in enumerate(self.project.step_statuses)
                      if status != StepStatus.COMPLETED.value), None)
        if first is None:
            self.log_emitted.emit("SUCCESS", "Tất cả các bước đã hoàn thành.")
            self.workflow_completed.emit()
            self.run_all_mode = False
            return True
        if first > 0 and self.project.step_statuses[first - 1] != StepStatus.COMPLETED.value:
            first = 0
            self.reset()
            self.run_all_mode = True
        return self.run_step(first)

    def pause(self) -> None:
        if self.active_step is not None and self.is_real:
            step = self.active_step
            if hasattr(self.service, "cancel_run"):
                self.service.cancel_run()
            self.is_paused = True
            self.paused_step = step
            self.run_all_mode = False
            self._set_status(step, StepStatus.PAUSED)
            self.log_emitted.emit("INFO", "Đang dừng tại checkpoint an toàn; Resume sẽ chạy lại từ cache.")
        elif self.active_step is not None and self.timer.isActive():
            self.timer.stop()
            self.is_paused = True
            self._set_status(self.active_step, StepStatus.PAUSED)
            self.log_emitted.emit("INFO", "Đã tạm dừng quy trình.")

    def resume(self) -> None:
        if self.is_real and self.is_paused:
            step = self.paused_step
            self.active_step = None
            self.is_paused = False
            self.paused_step = None
            if hasattr(self.service, "cancel"):
                from .core.models import CancellationToken
                self.service.cancel = CancellationToken()
            QTimer.singleShot(0, lambda: self.run_step(step if step is not None else 0))
        elif self.active_step is not None and self.is_paused:
            self.is_paused = False
            self._set_status(self.active_step, StepStatus.RUNNING)
            self.timer.start()
            self.log_emitted.emit("INFO", "Tiếp tục quy trình.")

    def stop(self) -> None:
        if self.active_step is None:
            return
        step = self.active_step
        self.timer.stop()
        if self.is_real and hasattr(self.service, "cancel_run"):
            self.service.cancel_run()
        self.active_step = None
        self.run_all_mode = False
        self.is_paused = False
        self.paused_step = None
        self.pending_step = None
        self._set_status(step, StepStatus.CANCELLED)
        self.log_emitted.emit("WARNING", f"Đã dừng bước {step + 1}.")

    def retry(self, step: int) -> bool:
        if self.active_step is not None:
            return False
        self.project.step_statuses[step] = StepStatus.READY.value
        return self.run_step(step)

    def reset_step(self, step: int) -> None:
        if self.active_step == step:
            self.stop()
        for index in range(step, 8):
            status = StepStatus.READY if index == step else StepStatus.PENDING
            self._set_status(index, status)
        self._emit_overall()
        self.dirty_changed.emit(True)

    def reset(self) -> None:
        self.timer.stop()
        self.active_step = None
        self.run_all_mode = False
        self.is_paused = False
        self.project.step_statuses = [StepStatus.READY.value, *([StepStatus.PENDING.value] * 7)]
        for index, status in enumerate(self.project.step_statuses):
            self.step_status_changed.emit(index, status)
            self.step_progress_changed.emit(index, 0)
        self._emit_overall()
        self.log_emitted.emit("INFO", "Đã đặt lại toàn bộ quy trình.")
        self.dirty_changed.emit(True)

    def _tick(self) -> None:
        if self.active_step is None:
            return
        self.progress = min(100, self.progress + 5)
        step = self.active_step
        self.step_progress_changed.emit(step, self.progress)
        self.overall_progress_changed.emit(round((step * 100 + self.progress) / 8))
        if self.progress % 25 == 0:
            self.log_emitted.emit("INFO", self.service.message_for(step, self.progress))
        if self.progress >= 100:
            self.timer.stop()
            self._set_status(step, StepStatus.COMPLETED)
            self.log_emitted.emit("SUCCESS", f"Hoàn thành bước {step + 1}: {STEP_TITLES[step]}.")
            self.active_step = None
            self.dirty_changed.emit(True)
            if step < 7:
                self._set_status(step + 1, StepStatus.READY)
            if self.run_all_mode and step < 7:
                QTimer.singleShot(180, lambda: self.run_step(step + 1))
            elif step == 7:
                self.run_all_mode = False
                self.workflow_completed.emit()

    def _set_status(self, step: int, status: StepStatus) -> None:
        self.project.step_statuses[step] = status.value
        self.step_status_changed.emit(step, status.value)

    def _emit_overall(self) -> None:
        completed = sum(s == StepStatus.COMPLETED.value for s in self.project.step_statuses)
        self.overall_progress_changed.emit(round(completed / 8 * 100))

    def _weighted_progress(self, step: int, value: int) -> int:
        weights = getattr(self.service, "WEIGHTS", (0.125,) * 8)
        completed = sum(weights[index] for index in range(step))
        return round((completed + weights[step] * value / 100) * 100)
