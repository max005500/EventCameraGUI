"""Raster event palettes; no native imports when listing them in the web UI."""
from functools import lru_cache

DEFAULT_PALETTE = "original"

# RGB values are for image pixels, not Flet widget styling.
EVENT_PALETTES = {
    "original": {
        "label": "Original", "background": (25, 25, 25),
        "on": (255, 210, 30), "off": (20, 70, 220),
        "description": "ON dorado · OFF azul · fondo oscuro",
    },
    "autumn": {
        "label": "Autumn", "background": (0, 0, 0), "colormap": "COLORMAP_AUTUMN",
        "description": "ON amarillo–naranja · OFF naranja–rojo · fondo negro",
    },
    "magma": {
        "label": "Magma", "background": (0, 0, 4), "colormap": "COLORMAP_MAGMA",
        "description": "ON crema–salmón · OFF magenta–violeta · fondo casi negro",
    },
    "petroleum": {
        "label": "Petróleo", "background": (30, 37, 53),
        "on": (255, 255, 255), "off": (0, 0, 250),
        "description": "ON blanco · OFF azul claro · fondo azul petróleo",
    },
}


@lru_cache(maxsize=4)
def palette_bgr_luts(key):
    """Two 256-entry BGR tables, indexed by temporal intensity, plus background."""
    import numpy as np

    palette = EVENT_PALETTES[key]
    background = np.array(palette["background"][::-1], dtype=np.float32)
    if "colormap" in palette:
        import cv2
        ramp = np.arange(256, dtype=np.uint8).reshape(256, 1)
        full = cv2.applyColorMap(ramp, getattr(cv2, palette["colormap"])).reshape(256, 3)
        # Positive polarity uses the upper half; negative uses the lower half.
        # Decay changes the shade within that band, then fades toward the background.
        on = full[np.linspace(128, 255, 256).astype(np.uint8)].astype(np.float32)
        off = full[np.linspace(0, 127, 256).astype(np.uint8)].astype(np.float32)
    else:
        on = np.tile(np.array(palette["on"][::-1], dtype=np.float32), (256, 1))
        off = np.tile(np.array(palette["off"][::-1], dtype=np.float32), (256, 1))
    for array in (on, off, background):
        array.setflags(write=False)
    return on, off, background
