import logging

import flet as ft

from views.eventCameraView import EventCameraView

LOG_LEVEL = logging.INFO
logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app").setLevel(LOG_LEVEL)


async def main(page: ft.Page):
    page.title = "Event Camera Viewer"
    page.padding = 20
    if not page.web:
        page.window.width = 1100
        page.window.height = 750
    camera_view = EventCameraView(page)
    page.add(camera_view.build())
    page.on_disconnect = camera_view.close
    if not page.web:
        page.window.prevent_close = True
        closing = False

        async def handle_window_event(event):
            nonlocal closing
            if event.type == ft.WindowEventType.CLOSE and not closing:
                closing = True
                if await camera_view.close():
                    await page.window.destroy()
                else:
                    closing = False

        page.window.on_event = handle_window_event
    camera_view.start_preview_updates()
    if camera_view.camera:
        await camera_view._refresh_cameras()


ft.run(main)
