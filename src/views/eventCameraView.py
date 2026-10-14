"""Camera controls. Native hardware imports are deferred outside the web preview."""
from __future__ import annotations

import asyncio
import logging

import flet as ft

from event_palettes import DEFAULT_PALETTE, EVENT_PALETTES

log = logging.getLogger(f"app.{__name__}")


class EventCameraView:
    def __init__(self, page: ft.Page):
        self._page = page
        self.camera = None
        self.native_error = None
        self._preview_task = None
        self._busy = False
        self._cv2 = None
        if not page.web:
            try:
                import cv2
                from eventModel import EventCameraController
                self._cv2 = cv2
                self.camera = EventCameraController()
            except Exception as exc:
                self.native_error = str(exc)
                log.exception("Native camera dependencies could not load")
        self.destination_folder = None
        self.folder_picker = ft.FilePicker() if self.camera else None
        self.status = ft.Text("Disconnected", size=16)
        self.fps_text = ft.Text("FPS: 0.0")
        self.event_rate_text = ft.Text("Events/s: 0")
        self.total_events_text = ft.Text("Total events: 0")
        self.folder_text = ft.Text("Destination: not selected")
        self.record_status = ft.Text("Recording: idle")
        self.record_size = ft.Text("RAW size: 0.0 MB · events: 0")
        self.preview = ft.Image(src="preview.svg", width=800, height=520,
                                fit=ft.BoxFit.CONTAIN, gapless_playback=True)
        self.camera_dropdown = ft.Dropdown(label="Event camera", disabled=not self.camera)
        self.tau_text = ft.Text("Accumulation τ: 100 ms")
        self.tau_slider = ft.Slider(min=1, max=500, divisions=499, value=100,
                                    label="{value} ms", on_change=self._tau_changed)
        self.style_dropdown = ft.Dropdown(label="Accumulation mode", value="exponential",
            options=[ft.DropdownOption(key=mode, text=mode.title())
                     for mode in ("exponential", "linear", "window")],
            on_select=self._style_changed)
        self.palette_dropdown = ft.Dropdown(
            label="Paleta de eventos", value=DEFAULT_PALETTE,
            options=[ft.DropdownOption(key=key, text=palette["label"])
                     for key, palette in EVENT_PALETTES.items()], on_select=self._palette_changed,
        )
        self.palette_description = ft.Text(EVENT_PALETTES[DEFAULT_PALETTE]["description"])
        self.hardware_buttons = []

    def _button(self, label, callback):
        button = ft.Button(content=label, on_click=callback, disabled=not self.camera)
        self.hardware_buttons.append(button)
        return button

    def build(self):
        notice = (
            "Browser preview: interface only. USB streaming and Faery recording require "
            "the native Windows or Linux app."
            if self._page.web else
            (f"Camera dependencies unavailable: {self.native_error}" if self.native_error else
             "Camera: neuromorphic-drivers · Recording: Faery RAW")
        )
        controls = ft.Container(width=300, padding=20, content=ft.Column(
            scroll=ft.ScrollMode.AUTO, controls=[
                ft.Text("Event Camera Control", size=22, weight=ft.FontWeight.BOLD),
                self.camera_dropdown,
                self._button("Refresh", self._refresh_cameras),
                ft.Row(wrap=True, controls=[self._button("Connect", self._connect_camera),
                    self._button("Disconnect", self._disconnect_camera)]),
                ft.Divider(),
                ft.Row(wrap=True, controls=[self._button("Start stream", self._start_camera),
                    self._button("Stop stream", self._stop_camera)]),
                ft.Divider(),
                ft.Text("Event accumulation", size=18, weight=ft.FontWeight.BOLD),
                self.style_dropdown, self.tau_text, self.tau_slider,
                self._button("Clear accumulated events", self._clear_events),
                ft.Divider(), self.palette_dropdown, self.palette_description,
                ft.Text("ON: aumento de intensidad · OFF: disminución. Solo afecta al visor, no al RAW."),
                ft.Divider(), self.status, self.fps_text, self.event_rate_text,
                self.total_events_text, ft.Divider(),
                ft.Text("Faery recording", size=18, weight=ft.FontWeight.BOLD),
                ft.Text("RAW · live preview uses the same reader"),
                self._button("Select destination folder", self._select_destination),
                self.folder_text,
                ft.Row(wrap=True, controls=[self._button("Start recording", self._start_recording),
                    self._button("Stop / save", self._stop_recording)]),
                self.record_status, self.record_size,
                ft.Text("One camera reader feeds the live preview and Faery's RAW writer. "
                        "Stop / save finalizes the file, then resumes normal acquisition."),
            ]))
        viewer = ft.Container(expand=True, padding=10, content=ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO, controls=[
                ft.Text("Live Event View", size=22, weight=ft.FontWeight.BOLD),
                ft.Text(notice), self.preview,
            ]))
        return ft.Row(expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                      controls=[controls, viewer])

    async def _action(self, function, success, *args):
        if self._busy or self.camera is None:
            return
        self._busy = True
        for button in self.hardware_buttons:
            button.disabled = True
        self.status.value = "Working…"
        self._page.update()
        try:
            result = await asyncio.to_thread(function, *args)
            self.status.value = success
            return result
        except Exception as exc:
            self.status.value = str(exc)
            log.exception("Camera action failed")
        finally:
            self._busy = False
            for button in self.hardware_buttons:
                button.disabled = False
            self._page.update()

    async def _refresh_cameras(self):
        if self.camera is None:
            return
        cameras = await self._action(self.camera.list_cameras,
                                    "Camera discovery complete.")
        if cameras is not None:
            self.camera_dropdown.options = [ft.DropdownOption(key=str(c["index"]), text=c["name"])
                                             for c in cameras]
            self.camera_dropdown.value = "0" if cameras else None
            self.status.value = f"{len(cameras)} camera(s) detected"
            self._page.update()

    async def _connect_camera(self):
        if self.camera_dropdown.value is None:
            self.status.value = "Refresh and select a camera first."
            self._page.update()
            return
        await self._action(self.camera.connect, "Connected", int(self.camera_dropdown.value))
        if self.camera.device is not None:
            self.status.value = f"Connected: {self.camera.width}×{self.camera.height}"
            self._page.update()

    async def _disconnect_camera(self):
        await self._action(self.camera.disconnect, "Disconnected")
        if self.camera.device is None:
            self.preview.src = "preview.svg"
            self._page.update()

    async def _start_camera(self):
        await self._action(self.camera.start, "Streaming")

    async def _stop_camera(self):
        await self._action(self.camera.stop, "Stream stopped")

    async def _select_destination(self):
        if self._busy or self.folder_picker is None:
            return
        try:
            selected = await self.folder_picker.get_directory_path(
                dialog_title="Select recording destination", initial_directory=self.destination_folder)
            if selected:
                self.destination_folder = selected
                self.folder_text.value = f"Destination: {selected}"
        except Exception as exc:
            self.status.value = f"Folder selection error: {exc}"
        self._page.update()

    async def _start_recording(self):
        if not self.destination_folder:
            self.status.value = "Select a destination folder first."
            self._page.update()
            return
        filename = await self._action(self.camera.start_recording,
                                     "Faery RAW recording active; shared live preview", self.destination_folder)
        if filename:
            self.record_status.value = f"Recording RAW: {filename}"
            self._page.update()

    async def _stop_recording(self):
        filename = await self._action(self.camera.stop_recording, "Recording saved")
        if filename:
            self.record_status.value = f"Saved: {filename}"
            self._page.update()

    def _tau_changed(self, event):
        value = float(event.control.value)
        if self.camera:
            self.camera.set_tau(value * 1000)
        self.tau_text.value = f"Accumulation τ: {value:.0f} ms"
        self._page.update()

    def _style_changed(self, event):
        if self.camera and event.control.value:
            self.camera.set_style(event.control.value)

    def _palette_changed(self, event):
        key = event.control.value
        if key in EVENT_PALETTES:
            if self.camera:
                self.camera.set_event_palette(key)
            self.palette_description.value = EVENT_PALETTES[key]["description"]
            self._page.update()

    def _clear_events(self):
        if self.camera:
            self.camera.clear_events()

    def start_preview_updates(self):
        if self.camera and (self._preview_task is None or self._preview_task.done()):
            self._preview_task = asyncio.create_task(self._update_preview())

    async def _encoded_frame(self):
        def encode():
            frame = self.camera.get_latest_frame()
            if frame is None:
                return None
            ok, data = self._cv2.imencode(".jpg", frame,
                                          [self._cv2.IMWRITE_JPEG_QUALITY, 90])
            return data.tobytes() if ok else None
        return await asyncio.to_thread(encode)

    async def _update_preview(self):
        try:
            while True:
                if self.camera.preview_active:
                    frame = await self._encoded_frame()
                    if frame:
                        self.preview.src = frame
                self.fps_text.value = f"FPS: {self.camera.fps:.1f}"
                self.event_rate_text.value = f"Events/s: {self.camera.event_rate:,.0f}"
                self.total_events_text.value = f"Total events: {self.camera.total_events:,}"
                state = self.camera.recorder.snapshot()
                health = state["health"]
                self.record_size.value = (
                    f"RAW size: {state['bytes'] / 1024**2:.1f} MB · "
                    f"events: {health.get('total_events', 0):,}\n"
                    f"Dropped packets: {health.get('dropped_packets', 0):,} · "
                    f"overflows: {health.get('overflow_count', 0):,}"
                )
                if state["saving"]:
                    self.record_status.value = "Finalizing RAW… returning to normal acquisition"
                elif state["error"]:
                    self.record_status.value = f"Recording error: {state['error']}"
                elif state["active"]:
                    self.record_status.value = f"Recording RAW: {state['filename']}"
                elif state["completed"]:
                    self.record_status.value = f"Saved RAW: {state['filename']}"
                if self.camera.last_error and not self._busy:
                    self.status.value = f"Capture error: {self.camera.last_error}"
                # All recording metrics are updated BEFORE the page update.
                self._page.update()
                await asyncio.sleep(0.033)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Preview update task stopped")

    async def close(self):
        while self._busy:
            await asyncio.sleep(0.1)
        self._busy = True
        for button in self.hardware_buttons:
            button.disabled = True
        if self.camera:
            try:
                state = self.camera.recorder.snapshot()
                if state["filename"]:
                    try:
                        await asyncio.to_thread(self.camera.stop_recording, False)
                    except Exception:
                        if self.camera.recorder.snapshot()["alive"]:
                            raise
                        log.exception("Recording failed; preserving partial RAW file on exit")
                await asyncio.to_thread(self.camera.disconnect)
            except Exception as exc:
                log.exception("Camera cleanup failed; keeping window open")
                self.status.value = f"Cannot close yet: {exc}"
                self._busy = False
                for button in self.hardware_buttons:
                    button.disabled = False
                self._page.update()
                return False
        if self._preview_task:
            self._preview_task.cancel()
        return True
