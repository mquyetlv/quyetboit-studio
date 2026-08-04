from quyetboit_studio.core import PipelineError, ProgressEvent, StepResult
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


def test_run_all_stops_after_step_6_for_manual_studio_and_export(qtbot):
    controller = WorkflowController(ProjectModel())
    controller.timer.setInterval(1)
    with qtbot.waitSignal(controller.automatic_run_completed, timeout=5000):
        controller.run_all()
    assert controller.project.step_statuses == [
        *([StepStatus.COMPLETED.value] * 6),
        StepStatus.READY.value,
        StepStatus.PENDING.value,
    ]


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


def test_real_run_all_calls_only_steps_1_to_6(qtbot):
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
    with qtbot.waitSignal(controller.automatic_run_completed, timeout=5000):
        controller.run_all()

    assert service.steps == list(range(6))
    assert project.step_statuses[6:] == [StepStatus.READY.value, StepStatus.PENDING.value]


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
