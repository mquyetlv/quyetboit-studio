from PySide6.QtCore import Qt

from quyetboit_studio.main_window import MainWindow
from quyetboit_studio.models import ProjectModel
from quyetboit_studio.studio import StudioDialog


def test_shell_sidebar_and_pages(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.minimumWidth() == 1100
    assert window.pages.count() == 2
    window.toggle_sidebar()
    assert window.sidebar.width() == 76
    assert window.vietsub_nav.toolTip() == "VietSub"
    qtbot.mouseClick(window.audio_nav, Qt.MouseButton.LeftButton)
    assert window.pages.currentIndex() == 1


def test_vietsub_has_eight_steps(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.vietsub.stack.count() == 8
    assert len(window.vietsub.step_rows) == 8


def test_custom_context_validation_state(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    page = window.vietsub.pages[3]
    page.context.setCurrentText("Tự cung cấp")
    assert page.custom.isVisibleTo(page)
    page.custom.setPlainText("Dịch tự nhiên, vui vẻ")
    assert window.project.custom_context == "Dịch tự nhiên, vui vẻ"
    window.dirty = False


def test_studio_apply_and_cancel(qtbot):
    project = ProjectModel()
    dialog = StudioDialog(project.studio_settings)
    qtbot.addWidget(dialog)
    captured = []
    dialog.settings_applied.connect(captured.append)
    dialog.add_layer("Logo")
    dialog.speed.setValue(1.25)
    dialog.apply()
    assert captured[0]["speed"] == 1.25
    assert "Logo" in captured[0]["layers"]


def test_studio_reset_and_undo(qtbot):
    dialog = StudioDialog(ProjectModel().studio_settings)
    qtbot.addWidget(dialog)
    dialog.add_layer("Logo")
    assert dialog.layers.count() == 4
    dialog.undo()
    assert dialog.layers.count() == 3
