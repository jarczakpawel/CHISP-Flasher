from __future__ import annotations

from dataclasses import dataclass

from chisp_flasher.core.errors import FrameError

MAGIC_REQ = b"\x57\xAB"
MAGIC_RSP = b"\x55\xAA"
HEADER_LEN = 6
MIN_FRAME_LEN = 7


@dataclass(slots=True)
class IspFrame:
    magic: bytes
    cmd: int
    code: int
    length: int
    data: bytes
    checksum: int

    @property
    def payload(self) -> bytes:
        return bytes([self.cmd, self.code]) + self.length.to_bytes(2, 'little') + self.data


def checksum(payload: bytes) -> int:
    return sum(payload) & 0xFF


def pack_request(payload: bytes) -> bytes:
    return MAGIC_REQ + payload + bytes([checksum(payload)])


def make_request_payload(cmd: int, data: bytes = b'') -> bytes:
    return bytes([cmd & 0xFF]) + len(data).to_bytes(2, 'little') + data



def pack_command(cmd: int, data: bytes = b'', code: int = 0x00) -> bytes:
    return pack_request(make_request_payload(cmd=cmd, data=data))


def parse_frame(frame: bytes, expected_magic: bytes = MAGIC_RSP) -> IspFrame:
    if len(frame) < MIN_FRAME_LEN:
        raise FrameError('frame too short')
    if frame[:2] != expected_magic:
        raise FrameError(f'bad magic: {frame[:2].hex()}')
    cmd = frame[2]
    code = frame[3]
    length = frame[4] | (frame[5] << 8)
    total = HEADER_LEN + length + 1
    if len(frame) != total:
        raise FrameError(f'bad frame length: got={len(frame)} expected={total}')
    payload = frame[2:-1]
    got = frame[-1]
    want = checksum(payload)
    if got != want:
        raise FrameError(f'bad checksum: got=0x{got:02x} want=0x{want:02x}')
    data = frame[6:-1]
    return IspFrame(magic=frame[:2], cmd=cmd, code=code, length=length, data=data, checksum=got)


def scan_frames(buf: bytes, expected_magic: bytes = MAGIC_RSP) -> tuple[list[IspFrame], bytes]:
    out: list[IspFrame] = []
    i = 0
    n = len(buf)
    while i + MIN_FRAME_LEN <= n:
        j = buf.find(expected_magic, i)
        if j < 0:
            break
        if j + MIN_FRAME_LEN > n:
            return out, buf[j:]
        length = buf[j + 4] | (buf[j + 5] << 8)
        total = HEADER_LEN + length + 1
        if j + total > n:
            return out, buf[j:]
        chunk = buf[j:j + total]
        try:
            out.append(parse_frame(chunk, expected_magic=expected_magic))
            i = j + total
        except FrameError:
            i = j + 1
    return out, buf[i:] if i < n else b''
