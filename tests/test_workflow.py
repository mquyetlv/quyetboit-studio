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


def test_run_all_completes(qtbot):
    controller = WorkflowController(ProjectModel())
    controller.timer.setInterval(1)
    with qtbot.waitSignal(controller.workflow_completed, timeout=5000):
        controller.run_all()
    assert controller.project.step_statuses == [StepStatus.COMPLETED.value] * 8

