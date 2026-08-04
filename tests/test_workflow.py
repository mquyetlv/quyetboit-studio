from quyetboit_studio.core import PartialFailureError, PipelineError, ProgressEvent, StepResult
from quyetboit_studio.models import ProjectModel, StepStatus
from quyetboit_studio.workflow import WorkflowController


def finish_active(controller):
    for _ in range(20):
        controller._tick()


def test_dependencies_and_single_step(qtbot):
    project = ProjectModel()
    controller = WorkflowController(project)
    assert controller.can_run(1)[0] is False
    assert controller.run_step(0)
    controller.timer.stop()
    finish_active(controller)
    assert project.step_statuses[0] == StepStatus.COMPLETED.value
    assert project.step_statuses[1] == StepStatus.READY.value


def test_pause_resume_and_stop(qtbot):
    controller = WorkflowController(ProjectModel())
    controller.run_step(0)
    controller.pause()
    assert controller.project.step_statuses[0] == StepStatus.PAUSED.value
    controller.resume()
    assert controller.project.step_statuses[0] == StepStatus.RUNNING.value
    controller.stop()
    assert controller.project.step_statuses[0] == StepStatus.CANCELLED.value


def test_reset_and_retry(qtbot):
    controller = WorkflowController(ProjectModel())
    controller.run_step(0); controller.stop()
    assert controller.retry(0)
    controller.stop(); controller.reset()
    assert controller.project.step_statuses == ["ready", *(["pending"] * 7)]


def test_run_all_completes_all_eight_steps(qtbot):
    controller = WorkflowController(ProjectModel())
    controller.timer.setInterval(1)
    with qtbot.waitSignal(controller.workflow_completed, timeout=5000):
        controller.run_all()
    assert controller.project.step_statuses == [StepStatus.COMPLETED.value] * 8


def test_real_pipeline_service_runs_off_ui_thread(qtbot):
    class Service:
        WEIGHTS = (0.125,) * 8

        def run_step(self, step, progress):
            progress(ProgressEvent(step, 0.5, "half"))
            return StepResult(step)

    project = ProjectModel()
    controller = WorkflowController(project, Service())
    assert controller.run_step(0)
    qtbot.waitUntil(lambda: controller.thread is None, timeout=3000)
    assert project.step_statuses[0] == StepStatus.COMPLETED.value
    assert project.step_statuses[1] == StepStatus.READY.value


def test_real_run_all_calls_all_eight_steps(qtbot):
    class Service:
        WEIGHTS = (0.125,) * 8

        def __init__(self):
            self.steps = []

        def run_step(self, step, progress):
            self.steps.append(step)
            return StepResult(step)

    project = ProjectModel()
    service = Service()
    controller = WorkflowController(project, service)
    with qtbot.waitSignal(controller.workflow_completed, timeout=5000):
        controller.run_all()

    assert service.steps == list(range(8))
    assert project.step_statuses == [StepStatus.COMPLETED.value] * 8


def test_retry_progress_and_final_api_detail_use_visible_log_levels(qtbot):
    controller = WorkflowController(ProjectModel())
    logs = []
    controller.log_emitted.connect(lambda level, message: logs.append((level, message)))

    controller._worker_progress(3, 20, "Gemini retry 2/4: HTTP 429", "retrying")
    error = PipelineError(
        "gemini_http_429",
        "Gemini đang giới hạn tốc độ.",
        provider="gemini",
        retryable=True,
        log_detail="HTTP 429; reason=RESOURCE_EXHAUSTED",
    )
    controller._worker_failed(3, error)

    assert logs[0] == ("WARNING", "Gemini retry 2/4: HTTP 429")
    assert logs[1] == (
        "ERROR",
        "Gemini đang giới hạn tốc độ. Chi tiết API: HTTP 429; reason=RESOURCE_EXHAUSTED",
    )


def test_partial_failure_is_persisted_for_retry_and_cleared_on_success(qtbot):
    project = ProjectModel()
    project.step_statuses[:3] = [StepStatus.COMPLETED.value] * 3
    controller = WorkflowController(project)
    changes = []
    controller.partial_failure_changed.connect(lambda step, value: changes.append((step, value)))
    error = PartialFailureError(
        "gemini_translation_partial",
        "Đã dịch thành công 125/126 cue; 1 cue thất bại.",
        completed=125,
        total=126,
        failed_item_ids=[66],
        provider="gemini",
    )

    controller._worker_failed(3, error)

    assert project.partial_failures["3"]["failed_item_ids"] == [66]
    assert controller.partial_failure(3)["completed"] == 125
    assert changes[-1][0] == 3

    controller._worker_succeeded(3, StepResult(3))

    assert controller.partial_failure(3) is None
    assert "3" not in project.partial_failures
