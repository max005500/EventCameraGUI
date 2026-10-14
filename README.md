# Event Camera Viewer

Native Windows/Linux Flet app. Browser preview shows the interface only; native camera acquisition and Faery recording must be tested on desktop.

## Files

- `src/main.py`: page, logging and controlled window closure.
- `src/views/eventCameraView.py`: controls, JPEG preview, file size and loss metrics.
- `src/eventModel.py`: normal acquisition, fixed-size time surface, snapshot rendering and handoff coordination.
- `src/camera_stream.py`: supplied `NeuromorphicCameraStream`, with stop signaling, packet validation, health metrics and a packet observer.
- `src/recording.py`: direct-to-disk Faery RAW worker and finalization.

## One reader, two outputs

Normal streaming uses the original neuromorphic-drivers acquisition loop. When recording starts, that loop is stopped and its device is released before the recording iterator opens the same serial. There is never a concurrent second camera reader.

During recording, Faery's `to_file` consumes `NeuromorphicCameraStream.__iter__`. For every packet, that iterator:

1. Checks loss/overflow metrics.
2. Calls the controller's packet observer to update a fixed-size accumulation surface (timestamps, polarity, seen pixels).
3. Yields the original decoded events, converted to `EVENTS_DTYPE`, to Faery's RAW writer.

The Flet preview task separately snapshots the accumulation surface, renders a frame and JPEG-encodes it off the acquisition thread, approximately 30 times per second depending on processing speed. Rendering is not performed for every event packet. The observer does not modify the packet yielded to Faery. All packets accepted by the iterator are passed to the writer, not only the packets that lead to displayed frames.

There is no whole-recording RAM buffer, unbounded event queue or `itertools.tee`. Memory for the preview is bounded by sensor dimensions. Acquisition and disk writing are still subject to CPU, disk and driver throughput; watch the loss/overflow counters. If the observer raises an error, it is disabled and the RAW recording continues; the UI reports the preview error.

## Workflow

1. Refresh, select a camera, Connect and Start stream.
2. Select a destination and Start recording.
3. Live preview and RAW writing share the single recording iterator.
4. Stop / save ends iteration cooperatively, lets Faery finalize, renames `.partial.raw` to the final file, then reconnects the normal viewer reader.
5. The normal stream resumes if it was active before recording. If recording began with normal streaming stopped, it returns to that stopped state.

There can be a brief preview interruption at the handoffs: the camera must close/reopen safely, and its timestamp origin may reset. The time surface is cleared at those transitions; total acquired events are retained across the handoff. FPS reports rendered preview frames, not acquisition packets. RAW file size may lag while Faery buffers writes.

## Event colors

The side panel's **Paleta de eventos** dropdown selects a complete color combination:

- **Original**: gold ON, blue OFF, dark background; retains the previous appearance.
- **Autumn**: OpenCV's Autumn colormap; ON uses its upper yellow/orange band, OFF its lower orange/red band, on black.
- **Magma**: OpenCV's Magma colormap; ON uses its upper cream/salmon band, OFF its lower magenta/violet band, on near-black.
- **Petróleo**: white ON, light-blue OFF, petroleum-blue background.

Autumn and Magma vary the shade with temporal decay within each polarity's band, then fade toward the background. Original and Petróleo retain a fixed polarity color and fade toward their background. `src/event_palettes.py` defines the presets and caches the native BGR lookup tables. Colors are raster pixel values, not app-theme styling.

Selections update the live renderer, including during shared-iterator recording. They do not change sensor events, timestamps, polarity or the RAW file. The selection is held in memory for the current app session.

## Safety and verification

Stop checks also run on timeout packets (50 ms iterator timeout), so a quiet camera can stop normally. Forced process termination may leave an incomplete file. Normal desktop window closure first attempts recording finalization and camera cleanup. If the worker is still alive after a timeout, the window stays open and the camera remains reserved.

Failed writes keep `.partial.raw` for diagnosis, without claiming completion. The RAW encoding is chosen by the installed Faery version; no EVT3 header is hand-built by the app.

The writer follows the supplied `camera.to_file("video.raw")` API. Verify simultaneous preview/recording, tau and accumulation controls, loss metrics, RAW playback, Stop / save and subsequent streaming on Windows and Linux with the actual sensor. Browser checks cannot validate those native paths.
