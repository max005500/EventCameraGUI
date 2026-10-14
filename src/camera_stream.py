"""Faery camera stream adapted from the supplied NeuromorphicCameraStream.

A stop Event ends iteration normally, allowing to_file to finalize its writer.
Only import this module on native desktop.
"""
from __future__ import annotations

import logging
import threading

import faery.events_stream as events_stream
import neuromorphic_drivers as nd

log = logging.getLogger(f"app.{__name__}")


class NeuromorphicCameraStream(events_stream.EventsStream):
    def __init__(self, serial=None, iterator_timeout=0.05, loss_policy="warn",
                 backlog_warning_threshold=None, stop_event=None, ready_event=None,
                 packet_observer=None):
        if loss_policy not in {"ignore", "warn", "raise"}:
            raise ValueError("Unknown loss policy.")
        if iterator_timeout is None or iterator_timeout <= 0:
            raise ValueError("A positive iterator_timeout is needed for controlled stopping.")
        self.nd = nd
        self.serial = serial
        self.iterator_timeout = iterator_timeout
        self.loss_policy = loss_policy
        self.backlog_warning_threshold = backlog_warning_threshold
        self.packet_observer = packet_observer
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.ready_event = ready_event if ready_event is not None else threading.Event()
        self._health_lock = threading.RLock()
        self._total_events = self._total_packets = 0
        self._last_dropped_packets = self._dropped_packets = 0
        self._overflow_count = self._max_backlog = 0
        self._max_delay = 0.0
        self._clutch_engaged_count = 0
        self.device_list = [d for d in nd.list_devices()
                            if d.error is None and (serial is None or d.serial == serial)]
        if not self.device_list:
            raise RuntimeError("No event camera found. Check USB access and camera drivers.")
        if self.serial is None:
            self.serial = self.device_list[0].serial
        with nd.open(serial=self.serial) as device:
            props = device.properties()
            self._dimensions = (int(props.width), int(props.height))
            self._device_name = device.name()

    def _handle_loss(self, message):
        if self.loss_policy == "warn":
            log.warning(message)
        elif self.loss_policy == "raise":
            raise RuntimeError(message)

    def _validate_packet(self, status, packet):
        with self._health_lock:
            ring = status.ring
            if ring is not None:
                backlog = ring.backlog
                self._max_backlog = max(self._max_backlog, backlog)
                if (self.backlog_warning_threshold is not None
                        and backlog >= self.backlog_warning_threshold):
                    log.warning("High neuromorphic-driver backlog: %d", backlog)
                delay = status.delay()
                if delay is not None:
                    self._max_delay = max(self._max_delay, delay)
                if ring.clutch_engaged:
                    self._clutch_engaged_count += 1
                    log.warning("Ring clutch engaged (backlog=%d)", backlog)
                dropped = ring.dropped_packets
                if dropped > self._last_dropped_packets:
                    delta = dropped - self._last_dropped_packets
                    self._dropped_packets += delta
                    self._handle_loss(f"USB packet loss: {delta} new dropped packets (total={dropped})")
                self._last_dropped_packets = dropped
            if packet is not None:
                indices = getattr(packet, "polarity_events_overflow_indices", None)
                if indices is not None and len(indices):
                    self._overflow_count += len(indices)
                    self._handle_loss(f"Polarity-event overflow: {len(indices)} point(s)")

    def __iter__(self):
        log.info("Faery recording stream: name=%s serial=%s", self._device_name, self.serial)
        with nd.open(serial=self.serial, iterator_timeout=self.iterator_timeout) as device:
            props = device.properties()
            self._dimensions = (int(props.width), int(props.height))
            self.ready_event.set()
            for status, packet in device:
                # Check even on timeout packets, so Stop works with a quiet sensor.
                if self.stop_event.is_set():
                    break
                self._validate_packet(status, packet)
                # One iteration supplies both the accumulator and Faery's writer.
                # Observer must not mutate the packet that is about to be yielded.
                if self.packet_observer is not None:
                    try:
                        self.packet_observer(status, packet)
                    except Exception:
                        log.exception("Preview observer failed; recording continues without preview")
                        self.packet_observer = None
                if packet is None or packet.polarity_events is None:
                    continue
                events = packet.polarity_events
                with self._health_lock:
                    self._total_packets += 1
                    self._total_events += len(events)
                yield events.astype(events_stream.EVENTS_DTYPE, copy=False)
        log.info("Faery camera iterator ended normally")

    def dimensions(self):
        return self._dimensions

    def health(self):
        with self._health_lock:
            return {
                "total_events": self._total_events,
                "total_packets": self._total_packets,
                "dropped_packets": self._dropped_packets,
                "overflow_count": self._overflow_count,
                "clutch_engaged_count": self._clutch_engaged_count,
                "max_backlog": self._max_backlog,
                "max_delay_seconds": self._max_delay,
                "data_loss_detected": self._dropped_packets > 0 or self._overflow_count > 0,
            }
