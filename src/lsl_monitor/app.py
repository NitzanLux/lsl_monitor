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
        self._dismissed_choices: dict[str, tuple[str, ...]] = {}
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
        self._request_stream_choices()
        snapshots = {worker.config.id: worker.snapshot() for worker in self.workers}
        for panel, worker in self.panels:
            panel.set_stream_label(getattr(worker, "display_name", worker.config.id))
            panel.update_snapshot(snapshots[worker.config.id])
        active = sum(snapshot.active for snapshot in snapshots.values())
        total = len(snapshots)
        self.status.setText(f"{active}/{total} streams active")
        self.status.setStyleSheet(
            f"color: {'#4ade80' if active == total else '#f87171'}; font-weight: 600;"
        )

    def _request_stream_choices(self) -> None:
        """Ask only when discovery finds equally good runtime candidates."""

        for worker in self.workers:
            options_method = getattr(worker, "selection_options", None)
            if options_method is None:
                continue
            options = options_method()
            signature = tuple(choice_id for choice_id, _ in options)
            if len(options) < 2 or self._dismissed_choices.get(worker.config.id) == signature:
                continue
            labels = [label for _, label in options]
            selected, accepted = QtWidgets.QInputDialog.getItem(
                self,
                "Choose LSL stream",
                f"Several outlets match {worker.config.id!r}.\n"
                "Choose the stream owned by this experiment:",
                labels,
                0,
                False,
            )
            if accepted:
                worker.choose_stream(options[labels.index(selected)][0])
                self._dismissed_choices.pop(worker.config.id, None)
            else:
                self._dismissed_choices[worker.config.id] = signature

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
        /* In-panel controls: the audio panel and the spectral frequency range. */
        QComboBox, QDoubleSpinBox, QPushButton {
          background: #0a131d;
          border: 1px solid #2b4054;
          border-radius: 5px;
          color: #d9e2ec;
          padding: 3px 6px;
        }
        QPushButton:hover { background: #24415a; }
        QComboBox QAbstractItemView {
          background: #0a131d;
          border: 1px solid #2b4054;
          color: #d9e2ec;
          selection-background-color: #0e7490;
        }
        QSlider::groove:horizontal {
          background: #16202c;
          border-radius: 2px;
          height: 4px;
        }
        QSlider::handle:horizontal {
          background: #67e8f9;
          border-radius: 6px;
          margin: -5px 0;
          width: 12px;
        }
        QStatusBar { border-top: 1px solid #263445; }
        """
    )
    workers = [
        LSLStreamWorker(
            stream,
            # Panels with their own time window decide how much a stream has to
            # keep, which can be more than the shared trace history.
            stream.sample_seconds(config.window.history_seconds),
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
