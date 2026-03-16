from __future__ import annotations

from dataclasses import dataclass

from chisp_flasher.core.errors import FrameError
from chisp_flasher.protocol.commands import (
    CFG_MASK_FULL,
    CFG_MASK_RDPR_USER_DATA_WPR,
    CMD_ERASE,
    CMD_IDENTIFY,
    CMD_ISP_END,
    CMD_ISP_KEY,
    CMD_PROGRAM,
    CMD_READ_CFG,
    CMD_SET_BAUD,
    CMD_VERIFY,
    CMD_WRITE_CFG,
    IDENTIFY_BANNER,
    u32_le,
)


@dataclass(slots=True)
class NativeUsbFrame:
    cmd: int
    code: int
    length: int
    data: bytes

    @property
    def raw(self) -> bytes:
        return bytes([self.cmd, self.code]) + self.length.to_bytes(2, 'little') + self.data


def make_request(cmd: int, data: bytes = b'') -> bytes:
    return bytes([cmd & 0xFF]) + len(data).to_bytes(2, 'little') + data


def make_frame(cmd: int, data: bytes = b'') -> bytes:
    return make_request(cmd, data)


def parse_frame(buf: bytes) -> NativeUsbFrame:
    if len(buf) < 4:
        raise FrameError('native usb frame too short')
    cmd = buf[0]
    code = buf[1]
    length = int.from_bytes(buf[2:4], 'little')
    if len(buf) != 4 + length:
        raise FrameError(f'native usb bad length: got={len(buf)} expected={4 + length}')
    return NativeUsbFrame(cmd=cmd, code=code, length=length, data=buf[4:])


def build_identify(device_id: int, device_type: int) -> bytes:
    data = bytes([device_id & 0xFF, device_type & 0xFF]) + IDENTIFY_BANNER
    return make_request(CMD_IDENTIFY, data)


def build_read_cfg(bit_mask: int = CFG_MASK_FULL) -> bytes:
    return make_request(CMD_READ_CFG, bytes([bit_mask & 0xFF, 0x00]))


def build_write_cfg(bit_mask: int = CFG_MASK_RDPR_USER_DATA_WPR, data: bytes = b'') -> bytes:
    return make_request(CMD_WRITE_CFG, bytes([bit_mask & 0xFF, 0x00]) + data)


def build_isp_key(seed: bytes) -> bytes:
    return make_request(CMD_ISP_KEY, seed)


def build_erase(sectors: int) -> bytes:
    return make_request(CMD_ERASE, u32_le(sectors))


def build_set_baud(baud: int) -> bytes:
    return make_request(CMD_SET_BAUD, u32_le(baud))


def build_program(address: int, pad_byte: int, data: bytes) -> bytes:
    return make_request(CMD_PROGRAM, u32_le(address) + bytes([pad_byte & 0xFF]) + data)


def build_verify(address: int, pad_byte: int, data: bytes) -> bytes:
    return make_request(CMD_VERIFY, u32_le(address) + bytes([pad_byte & 0xFF]) + data)


def build_isp_end(reason: int) -> bytes:
    return make_request(CMD_ISP_END, bytes([reason & 0xFF]))
