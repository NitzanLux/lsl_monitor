"""Qt application assembly and responsive multi-panel layout."""

from __future__ import annotations

import sys

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from lsl_monitor.config import (
    ConfigError,
    MonitorConfig,
    configured_view_positions,
)
from lsl_monitor.layout import PanelCanvas, RelativeRect
from lsl_monitor.lsl import LSLStreamWorker
from lsl_monitor.views import MonitorPanel, create_panel


class MonitorWindow(QtWidgets.QMainWindow):
    """Top-level window containing all configured stream views."""

    def __init__(self, config: MonitorConfig, workers: list[LSLStreamWorker]) -> None:
        super().__init__()
        self.config = config
        self.workers = workers
        self.panels: list[tuple[MonitorPanel, LSLStreamWorker]] = []
        self.setWindowTitle(config.window.title)
        self.resize(1400, 900)

        self.canvas = PanelCanvas(columns=config.window.columns)
        self.setCentralWidget(self.canvas)

        for stream, worker in zip(config.streams, workers, strict=True):
            for view in stream.views:
                positions = configured_view_positions(view.channels, stream.channels)
                # The stream is named separately in the header, so the default
                # title only has to say what the panel shows.
                title = view.title or view.type
                panel = create_panel(
                    title,
                    view,
                    positions,
                    config.window.history_seconds,
                    stream_id=stream.id,
                )
                rectangle = (
                    RelativeRect(
                        view.layout.x,
                        view.layout.y,
                        view.layout.width,
                        view.layout.height,
                    )
                    if view.layout
                    else None
                )
                self.canvas.add_panel(panel, rectangle)
                self.panels.append((panel, worker))

        self.status = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.status, 1)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(max(10, round(1000.0 / config.window.refresh_hz)))

    @QtCore.Slot()
    def refresh(self) -> None:
        snapshots = {worker.config.id: worker.snapshot() for worker in self.workers}
        for panel, worker in self.panels:
            panel.update_snapshot(snapshots[worker.config.id])
        active = sum(snapshot.active for snapshot in snapshots.values())
        total = len(snapshots)
        self.status.setText(f"{active}/{total} streams active")
        self.status.setStyleSheet(
            f"color: {'#4ade80' if active == total else '#f87171'}; font-weight: 600;"
        )

    def closeEvent(self, event: object) -> None:
        self.timer.stop()
        for worker in self.workers:
            worker.stop()
        super().closeEvent(event)


def run(config: MonitorConfig) -> int:
    """Create workers, start the monitor, and run the Qt event loop."""

    pg.setConfigOptions(antialias=False)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    application.setStyle("Fusion")
    application.setStyleSheet(
        """
        QMainWindow, QWidget { background: #0b1118; color: #d9e2ec; }
        QFrame#monitorPanel {
          background: #101720;
          border: 1px solid #263445;
          border-radius: 7px;
        }
        QLabel#panelTitle {
          color: #f0f4f8;
          font-size: 13px;
          font-weight: 600;
          padding: 2px;
        }
        QLabel#panelStream {
          color: #8ba2b8;
          background: #16202c;
          border: 1px solid #263445;
          border-radius: 4px;
          font-size: 11px;
          font-weight: 600;
          padding: 1px 6px;
        }
        QStatusBar { border-top: 1px solid #263445; }
        """
    )
    workers = [
        LSLStreamWorker(
            stream,
            config.window.history_seconds,
            config.window.inactive_after_seconds,
            config.window.max_points_per_channel,
        )
        for stream in config.streams
    ]
    try:
        window = MonitorWindow(config, workers)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    for worker in workers:
        worker.start()
    window.show()
    return application.exec()
