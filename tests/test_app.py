import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from lsl_monitor.app import MonitorWindow
from lsl_monitor.config import StreamConfig, load_config
from lsl_monitor.mock import make_mock_snapshot

ROOT = Path(__file__).resolve().parents[1]


class MockWorker:
    def __init__(self, config: StreamConfig) -> None:
        self.config = config
        self.stopped = False

    def snapshot(self):
        return make_mock_snapshot(self.config, now_lsl_time=100.0, history_seconds=10.0)

    def stop(self) -> None:
        self.stopped = True


def test_monitor_window_uses_responsive_custom_panel_geometry() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    config = load_config(ROOT / "json" / "example.monitor.json")
    workers = [MockWorker(stream) for stream in config.streams]
    window = MonitorWindow(config, workers)
    window.resize(1200, 800)
    window.show()
    application.processEvents()
    window.refresh()

    trace_geometry = window.panels[0][0].geometry()
    plane_geometry = window.panels[1][0].geometry()
    assert len(window.panels) == 6
    assert trace_geometry.width() > plane_geometry.width()
    assert trace_geometry.x() < plane_geometry.x()
    window.close()
    assert all(worker.stopped for worker in workers)
