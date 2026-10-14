"""One camera reader at a time; accumulation shared by preview and Faery."""
from __future__ import annotations

import logging
import threading
import time

import neuromorphic_drivers as nd
import numpy as np

from recording import FaeryRecorder
from event_palettes import DEFAULT_PALETTE, EVENT_PALETTES, palette_bgr_luts

log = logging.getLogger(f"app.{__name__}")


class EventCameraController:
    def __init__(self):
        self.device = None
        self.width, self.height = 640, 480
        self.running = False
        self.thread = None
        self.frame_lock = threading.RLock()
        self.timestamps = self.polarities = self.seen = None
        self.serial = None
        self.current_t = 0
        self.tau_us = 100_000.0
        self.style = "exponential"
        self.event_palette = DEFAULT_PALETTE
        self.fps = self.event_rate = 0.0
        self.total_events = 0
        self.last_error = None
        self._sample_started = time.perf_counter()
        self._sample_events = self._sample_frames = 0
        self.recorder = FaeryRecorder()
        self._camera_choices = []
        self._recording_handoff = False
        self._resume_stream = False

    @property
    def preview_active(self):
        return self.running or self.recorder.snapshot()["active"]

    def list_cameras(self):
        self._camera_choices = [d for d in nd.list_devices() if d.error is None]
        return [{"index": i, "name": f"Event camera · {d.serial}"}
                for i, d in enumerate(self._camera_choices)]

    def _reset_accumulation(self, reset_total=False):
        with self.frame_lock:
            shape = (self.height, self.width)
            self.timestamps = np.zeros(shape, dtype=np.float64)
            self.polarities = np.zeros(shape, dtype=bool)
            self.seen = np.zeros(shape, dtype=bool)
            self.current_t = 0
            if reset_total:
                self.total_events = 0
            self._sample_started = time.perf_counter()
            self._sample_events = self._sample_frames = 0
            self.fps = self.event_rate = 0.0

    def connect(self, index=0, serial=None, reset_total=True):
        if self._recording_handoff or self.recorder.snapshot()["alive"]:
            raise RuntimeError("Stop / save Faery recording before connecting another reader.")
        if self.device is not None:
            return
        if serial is None:
            if not self._camera_choices:
                self.list_cameras()
            if index < 0 or index >= len(self._camera_choices):
                raise RuntimeError("Selected camera is no longer available. Refresh the list.")
            serial = self._camera_choices[index].serial
        device = nd.open(serial=serial, iterator_timeout=1.0 / 60.0)
        try:
            props = device.properties()
            self.width, self.height = props.width, props.height
            self.serial = device.serial()
            self._reset_accumulation(reset_total=reset_total)
            self.device = device
            self.last_error = None
            log.info("Connected %s (%dx%d)", self.serial, self.width, self.height)
        except Exception:
            device.__exit__(None, None, None)
            raise

    def disconnect(self):
        if self._recording_handoff or self.recorder.snapshot()["alive"]:
            raise RuntimeError("Stop / save the recording before disconnecting.")
        self.stop()
        if self.device is not None:
            self.device.__exit__(None, None, None)
            self.device = None
        with self.frame_lock:
            self.timestamps = self.polarities = self.seen = None
            self.fps = self.event_rate = 0.0

    def start(self):
        if self._recording_handoff or self.recorder.snapshot()["alive"]:
            raise RuntimeError("Preview is supplied by Faery's reader; no second reader is needed.")
        if self.device is None:
            raise RuntimeError("Connect to an event camera first.")
        if self.thread is not None and self.thread.is_alive():
            if self.running:
                return
            raise RuntimeError("Previous acquisition worker has not stopped yet.")
        self.running = True
        self.last_error = None
        self.thread = threading.Thread(target=self._capture_loop,
                                       name="event-camera-capture", daemon=True)
        self.thread.start()

    def stop(self):
        if self._recording_handoff or self.recorder.snapshot()["alive"]:
            raise RuntimeError("Use Stop / save to end the shared recording reader first.")
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=3.0)
            if self.thread.is_alive():
                raise RuntimeError("Camera worker did not stop; device has not been released.")
            self.thread = None
        self.fps = self.event_rate = 0.0

    def start_recording(self, destination):
        if self._recording_handoff:
            raise RuntimeError("Stop / save the previous recording first.")
        if self.device is None:
            raise RuntimeError("Connect to an event camera first.")
        resume = self.running
        self.stop()
        self.device.__exit__(None, None, None)
        self.device = None
        self._resume_stream = resume
        self._recording_handoff = True
        # Device reopening may reset the sensor time origin; remove stale timestamps.
        self._reset_accumulation()
        self.last_error = None
        try:
            return self.recorder.start(destination, self.serial, self._recording_packet)
        except Exception:
            if not self.recorder.snapshot()["alive"]:
                self._restore_preview()
            raise

    def _restore_preview(self):
        self._recording_handoff = False
        serial, resume = self.serial, self._resume_stream
        self._resume_stream = False
        self.connect(serial=serial, reset_total=False)
        if resume:
            self.start()

    def stop_recording(self, resume=True):
        try:
            return self.recorder.stop_and_save()
        finally:
            if self._recording_handoff and not self.recorder.snapshot()["alive"]:
                if resume:
                    self._restore_preview()
                else:
                    self._recording_handoff = False
                    self._resume_stream = False

    def _capture_loop(self):
        try:
            for status, packet in self.device:
                if not self.running:
                    break
                self._consume_packet(status, packet)
        except Exception as exc:
            if self.running:
                self.last_error = str(exc)
                log.exception("Camera capture failed")
        finally:
            self.running = False

    def _recording_packet(self, status, packet):
        # Called synchronously by the ONE iterator consumed by Faery.to_file.
        # Only update the fixed-size time surface here; rendering happens separately.
        try:
            self._consume_packet(status, packet)
        except Exception as exc:
            self.last_error = f"Shared preview failed: {exc}"
            raise  # The stream disables this observer, but keeps recording.

    def _consume_packet(self, status, packet):
        events = None if packet is None else packet.polarity_events
        ring = status.ring
        current_t = None if ring is None else ring.current_t
        with self.frame_lock:
            if self.timestamps is None:
                return
            if current_t is not None:
                self.current_t = current_t
            elif events is not None and len(events):
                self.current_t = int(events["t"][-1])
            if events is not None and len(events):
                self._push_events(events)
                self.total_events += len(events)
                self._sample_events += len(events)
            self._update_metrics()

    def _update_metrics(self):
        now = time.perf_counter()
        elapsed = now - self._sample_started
        if elapsed >= 1.0:
            self.event_rate = self._sample_events / elapsed
            self.fps = self._sample_frames / elapsed
            self._sample_events = self._sample_frames = 0
            self._sample_started = now

    def _push_events(self, events):
        x = events["x"].astype(np.int32, copy=False)
        y = events["y"].astype(np.int32, copy=False)
        valid = (x >= 0) & (x < self.width) & (y >= 0) & (y < self.height)
        x, y = x[valid], y[valid]
        flat = y * self.width + x
        _, reversed_indices = np.unique(flat[::-1], return_index=True)
        indices = len(flat) - 1 - reversed_indices
        x, y = x[indices], y[indices]
        self.timestamps[y, x] = events["t"][valid][indices]
        self.polarities[y, x] = events["on"][valid][indices]
        self.seen[y, x] = True

    @staticmethod
    def _render_frame(timestamps, polarities, seen, current_t, tau, style, palette_key):
        dt = np.maximum(current_t - timestamps, 0.0)
        tau = max(tau, 1.0)
        if style == "exponential":
            decay = np.exp(-dt / tau)
        elif style == "linear":
            decay = np.maximum(1.0 - dt / tau, 0.0)
        else:
            decay = (dt < tau).astype(np.float32)
        decay[~seen] = 0.0
        on_lut, off_lut, background = palette_bgr_luts(palette_key)
        intensity = np.clip(decay * 255.0, 0, 255).astype(np.uint8)
        colors = np.where(polarities[..., None], on_lut[intensity], off_lut[intensity])
        frame = background + decay[..., None] * (colors - background)
        return np.clip(frame, 0, 255).astype(np.uint8)

    def get_latest_frame(self):
        # Snapshot quickly, then render/JPEG encode outside the acquisition lock.
        # No event queue: memory stays bounded by the sensor dimensions.
        with self.frame_lock:
            if self.timestamps is None:
                return None
            snapshot = (self.timestamps.copy(), self.polarities.copy(), self.seen.copy(),
                        self.current_t, self.tau_us, self.style, self.event_palette)
        frame = self._render_frame(*snapshot)
        with self.frame_lock:
            self._sample_frames += 1
            self._update_metrics()
        return frame

    def set_tau(self, tau_us):
        with self.frame_lock:
            self.tau_us = max(float(tau_us), 1.0)

    def set_style(self, style):
        if style not in {"exponential", "linear", "window"}:
            raise ValueError(f"Unknown event style: {style}")
        with self.frame_lock:
            self.style = style

    def set_event_palette(self, key):
        if key not in EVENT_PALETTES:
            raise ValueError("Unknown event palette.")
        with self.frame_lock:
            self.event_palette = key

    def clear_events(self):
        with self.frame_lock:
            if self.timestamps is not None:
                self.timestamps.fill(0)
                self.polarities.fill(False)
                self.seen.fill(False)
