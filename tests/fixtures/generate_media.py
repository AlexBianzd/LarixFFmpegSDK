"""Generate deterministic 2x2 AVI video and mono PCM WAV without external tools."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import wave


def _chunk(identifier: bytes, payload: bytes) -> bytes:
    padding = b"\x00" if len(payload) % 2 else b""
    return identifier + struct.pack("<I", len(payload)) + payload + padding


def _list_chunk(identifier: bytes, payload: bytes) -> bytes:
    return _chunk(b"LIST", identifier + payload)


def generate_avi(path: Path) -> None:
    pixels = (
        b"\x00\x00\xff\x00\xff\x00\x00\x00"
        b"\xff\x00\x00\xff\xff\xff\x00\x00"
    )
    avih = struct.pack(
        "<14I", 40000, 400, 0, 0x10, 1, 0, 1, 16, 2, 2, 0, 0, 0, 0
    )
    strh = struct.pack(
        "<4s4sIHHIIIIIIIIhhhh",
        b"vids", b"DIB ", 0, 0, 0, 0, 1, 25, 0, 1, 16, 0xFFFFFFFF, 0, 0, 0, 2, 2,
    )
    strf = struct.pack(
        "<IiiHHIIiiII", 40, 2, 2, 1, 24, 0, len(pixels), 2835, 2835, 0, 0
    )
    headers = _chunk(b"avih", avih) + _list_chunk(
        b"strl", _chunk(b"strh", strh) + _chunk(b"strf", strf)
    )
    movi = _list_chunk(b"movi", _chunk(b"00db", pixels))
    index = _chunk(b"idx1", struct.pack("<4sIII", b"00db", 0x10, 4, len(pixels)))
    body = _list_chunk(b"hdrl", headers) + movi + index
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body) + 4) + b"AVI " + body)


def generate_fixtures(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    video = directory / 'video.avi'
    audio = directory / 'audio.wav'
    generate_avi(video)
    samples = tuple(int(16000 * ((index % 16) - 8) / 8) for index in range(480))
    with wave.open(str(audio), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(struct.pack(f'<{len(samples)}h', *samples))
    return video, audio


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    generate_fixtures(arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
