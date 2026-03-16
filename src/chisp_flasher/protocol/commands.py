from __future__ import annotations

import struct

from chisp_flasher.protocol.framing import pack_command

CMD_IDENTIFY = 0xA1
CMD_ISP_END = 0xA2
CMD_ISP_KEY = 0xA3
CMD_ERASE = 0xA4
CMD_PROGRAM = 0xA5
CMD_VERIFY = 0xA6
CMD_READ_CFG = 0xA7
CMD_WRITE_CFG = 0xA8
CMD_SET_BAUD = 0xC5

CFG_MASK_RDPR_USER_DATA_WPR = 0x07
CFG_MASK_FULL = 0x1F
IDENTIFY_BANNER = b"MCU ISP & WCH.CN"



def u32_le(x: int) -> bytes:
    return struct.pack('<I', x & 0xFFFFFFFF)


def build_identify(device_id: int, device_type: int) -> bytes:
    data = bytes([device_id & 0xFF, device_type & 0xFF]) + IDENTIFY_BANNER
    return pack_command(CMD_IDENTIFY, data)


def build_read_cfg(bit_mask: int = CFG_MASK_FULL) -> bytes:
    return pack_command(CMD_READ_CFG, bytes([bit_mask & 0xFF, 0x00]))


def build_write_cfg(bit_mask: int, data: bytes) -> bytes:
    return pack_command(CMD_WRITE_CFG, bytes([bit_mask & 0xFF, 0x00]) + data)


def build_isp_key(seed: bytes) -> bytes:
    return pack_command(CMD_ISP_KEY, seed)


def build_erase(sectors: int) -> bytes:
    return pack_command(CMD_ERASE, u32_le(sectors))


def build_set_baud(baud: int) -> bytes:
    return pack_command(CMD_SET_BAUD, u32_le(baud))


def build_program(address: int, pad_byte: int, data: bytes) -> bytes:
    return pack_command(CMD_PROGRAM, u32_le(address) + bytes([pad_byte & 0xFF]) + data)


def build_verify(address: int, pad_byte: int, data: bytes) -> bytes:
    return pack_command(CMD_VERIFY, u32_le(address) + bytes([pad_byte & 0xFF]) + data)


def build_isp_end(reason: int) -> bytes:
    return pack_command(CMD_ISP_END, bytes([reason & 0xFF]))
