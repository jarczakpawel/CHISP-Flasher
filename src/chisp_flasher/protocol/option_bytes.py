from __future__ import annotations

from dataclasses import dataclass

from chisp_flasher.core.errors import FrameError


@dataclass(slots=True)
class OptionBytesState:
    raw_response: bytes
    cfg12: bytes
    rdpr_user: bytes
    data_bytes: bytes
    data: bytes
    wpr: bytes
    extra4: bytes
    uid: bytes


def parse_read_cfg_response(data: bytes) -> OptionBytesState:
    if len(data) < 14:
        raise FrameError(f'READ_CFG response too short: {len(data)}')
    cfg12 = bytes(data[2:14])
    rdpr_user = cfg12[0:4]
    data_bytes = cfg12[4:8]
    wpr = cfg12[8:12]
    extra4 = bytes(data[14:18]) if len(data) >= 18 else b''
    uid = bytes(data[-8:]) if len(data) >= 8 else b''
    return OptionBytesState(
        raw_response=bytes(data),
        cfg12=cfg12,
        rdpr_user=rdpr_user,
        data_bytes=data_bytes,
        data=data_bytes,
        wpr=wpr,
        extra4=extra4,
        uid=uid,
    )
