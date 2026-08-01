from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtCore import QObject, QTimer, Signal

from .models import STEP_TITLES, ProjectModel, StepStatus


class PipelineService(ABC):
    @abstractmethod
    def message_for(self, step: int, progress: int) -> str: ...


class MockPipelineService(PipelineService):
    def message_for(self, step: int, progress: int) -> str:
        verbs = ("Phân tích nguồn", "Chia đoạn audio", "Tạo phụ đề", "Dịch nội dung",
                 "Tổng hợp giọng", "Đồng bộ âm thanh", "Áp dụng hiệu ứng", "Render video")
        return f"{verbs[step]} — {progress}% (mô phỏng)"


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

    def can_run(self, step: int) -> tuple[bool, str]:
        if not 0 <= step < 8:
            return False, "Bước không hợp lệ."
        if step > 0 and self.project.step_statuses[step - 1] != StepStatus.COMPLETED.value:
            return False, f"Hãy hoàn thành bước {step} trước."
        if self.active_step is not None:
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
        self._set_status(step, StepStatus.RUNNING)
        self.log_emitted.emit("INFO", f"Bắt đầu bước {step + 1}: {STEP_TITLES[step]}.")
        self.timer.start()
        return True

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
        if self.active_step is not None and self.timer.isActive():
            self.timer.stop()
            self.is_paused = True
            self._set_status(self.active_step, StepStatus.PAUSED)
            self.log_emitted.emit("INFO", "Đã tạm dừng quy trình.")

    def resume(self) -> None:
        if self.active_step is not None and self.is_paused:
            self.is_paused = False
            self._set_status(self.active_step, StepStatus.RUNNING)
            self.timer.start()
            self.log_emitted.emit("INFO", "Tiếp tục quy trình.")

    def stop(self) -> None:
        if self.active_step is None:
            return
        step = self.active_step
        self.timer.stop()
        self.active_step = None
        self.run_all_mode = False
        self.is_paused = False
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
