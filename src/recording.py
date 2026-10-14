"""Direct Faery RAW recording worker. No whole-recording RAM buffer."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from camera_stream import NeuromorphicCameraStream

log = logging.getLogger(f"app.{__name__}")


class FaeryRecorder:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.thread = None
        self.stream = None
        self.active = self.saving = self.completed = False
        self.filename = self.partial_filename = self.error = None

    def start(self, destination, serial, packet_observer=None):
        folder = Path(destination)
        if not folder.is_dir():
            raise ValueError("Destination folder does not exist.")
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError("Faery recording is already running.")
            self.stop_event.clear()
            self.ready_event.clear()
            self.stream = None
            self.error = None
            self.completed = False
            name = datetime.now().strftime("event_camera_%Y%m%d_%H%M%S_%f.raw")
            self.filename = str(folder / name)
            self.partial_filename = str(folder / (Path(name).stem + ".partial.raw"))
            self.active = True
            self.saving = False
            self.thread = threading.Thread(target=self._record, args=(serial, packet_observer),
                                           name="faery-raw-recorder", daemon=True)
            self.thread.start()
        # Report constructor/writer/open errors rather than blindly saying Recording.
        if not self.ready_event.wait(timeout=10.0):
            self.stop_event.set()
            raise RuntimeError("Faery startup timed out. Use Stop / save before reconnecting.")
        with self.lock:
            if self.error:
                raise RuntimeError(f"Faery startup failed: {self.error}")
            return self.filename

    def _record(self, serial, packet_observer):
        try:
            stream = NeuromorphicCameraStream(
                serial=serial, iterator_timeout=0.05, loss_policy="warn",
                stop_event=self.stop_event, ready_event=self.ready_event,
                packet_observer=packet_observer,
            )
            with self.lock:
                self.stream = stream
            # This is the writer API provided by the user. It blocks until iteration ends.
            stream.to_file(self.partial_filename)
            temporary = Path(self.partial_filename)
            if not temporary.is_file():
                raise RuntimeError("Faery returned without producing the RAW file.")
            temporary.replace(self.filename)
            with self.lock:
                self.completed = True
            log.info("RAW recording finalized: %s; health=%s", self.filename, stream.health())
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
            # Keep partial output for diagnosis; never label it finalized.
            log.exception("Faery recording failed (partial file: %s)", self.partial_filename)
        finally:
            with self.lock:
                self.active = False
                self.saving = False
            self.ready_event.set()

    def stop_and_save(self):
        with self.lock:
            if self.thread is None:
                raise RuntimeError("No Faery recording has been started.")
            self.stop_event.set()
            thread = self.thread
            self.saving = thread.is_alive()
        thread.join(timeout=15.0)
        if thread.is_alive():
            # Do not terminate the worker or hand its camera to another owner.
            raise RuntimeError("Faery is still finalizing. Retry Stop / save; camera remains reserved.")
        with self.lock:
            self.saving = False
            if self.error:
                raise RuntimeError(f"Faery recording failed: {self.error}. Check the partial RAW file.")
            if not self.completed:
                raise RuntimeError("RAW file was not finalized.")
            return self.filename

    def snapshot(self):
        with self.lock:
            state = dict(active=self.active, saving=self.saving,
                         alive=self.thread is not None and self.thread.is_alive(),
                         completed=self.completed, error=self.error,
                         filename=self.filename, partial_filename=self.partial_filename)
            stream = self.stream
        state["health"] = stream.health() if stream is not None else {}
        path = state["filename"] if state["completed"] else state["partial_filename"]
        try:
            state["bytes"] = Path(path).stat().st_size if path else 0
        except OSError:
            state["bytes"] = 0
        return state
