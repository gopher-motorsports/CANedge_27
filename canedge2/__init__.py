"""CANedge2 -> GopherCAN data pipeline.

The CANedge2 is a standalone CAN logger (closed firmware, MF4 files on an SD card).
There is no firmware to write for it; this package is everything *around* it:
decode raw frames using the GopherCAN network YAML, and feed the results into
the existing Gopher Motorsports tooling (.gdat / .ld via gopher-vision).
"""
__version__ = "0.1.0"
