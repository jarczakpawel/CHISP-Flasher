from __future__ import annotations

from dataclasses import dataclass
from typing import Any


X103_BIT_IWDG_SW = 1 << 0
X103_BIT_STOP_RST = 1 << 1
X103_BIT_STANDBY_RST = 1 << 2
X103_BIT_PORCTR = 1 << 5
X035_RST_MASK = 0x18

FV_BIT_IWDG_SW = 1 << 2
FV_BIT_STOP_RST = 1 << 3
FV_BIT_STANDBY_RST = 1 << 4
FV_RAM_MASK = 0xC0

V20X_RAM_DECODE = {
    0x00: 'RAMX 64KB + ROM 128KB',
    0x40: 'RAMX 48KB + ROM 144KB',
    0x80: 'RAMX 32KB + ROM 160KB',
    0xC0: 'RAMX 32KB + ROM 160KB',
}
V20X_RAM_ENCODE = {
    'RAMX 64KB + ROM 128KB': 0x00,
    'RAMX 48KB + ROM 144KB': 0x40,
    'RAMX 32KB + ROM 160KB': 0x80,
}
V30X_RAM_DECODE = {
    0x00: 'RAMX 128KB + ROM 192KB',
    0x40: 'RAMX 96KB + ROM 224KB',
    0x80: 'RAMX 64KB + ROM 256KB',
    0xC0: 'RAMX 32KB + ROM 288KB',
}
V30X_RAM_ENCODE = {
    'RAMX 128KB + ROM 192KB': 0x00,
    'RAMX 96KB + ROM 224KB': 0x40,
    'RAMX 64KB + ROM 256KB': 0x80,
    'RAMX 32KB + ROM 288KB': 0xC0,
}


@dataclass(slots=True)
class WriteConfigResult:
    cfg12: bytes
    applied_fields: list[str]
    preserved_fields: list[str]


def _parse_hex_byte(value: Any, default: int) -> int:
    s = str(value or '').strip()
    if not s:
        return default & 0xFF
    return int(s, 0) & 0xFF


def _as_bool(value: Any) -> bool:
    return bool(value)


def _ram_maps(option_profile: str):
    key = (option_profile or '').strip().lower()
    if key in {'fv20x', 'fv20x_or_compact'}:
        return V20X_RAM_DECODE, V20X_RAM_ENCODE
    if key in {'fv30x'}:
        return V30X_RAM_DECODE, V30X_RAM_ENCODE
    return {}, {}


def decode_config_fields(option_profile: str, cfg12: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if len(cfg12) < 12:
        return out
    user0 = cfg12[2]
    out['data0_hex'] = f'0x{cfg12[4]:02X}'
    out['data1_hex'] = f'0x{cfg12[5]:02X}'
    out['wrp0_hex'] = f'0x{cfg12[8]:02X}'
    out['wrp1_hex'] = f'0x{cfg12[9]:02X}'
    out['wrp2_hex'] = f'0x{cfg12[10]:02X}'
    out['wrp3_hex'] = f'0x{cfg12[11]:02X}'

    key = (option_profile or '').strip().lower()
    if key == 'x103':
        out['enable_soft_ctrl_iwdg'] = bool(user0 & X103_BIT_IWDG_SW)
        out['disable_stop_mode_rst'] = bool(user0 & X103_BIT_STOP_RST)
        out['disable_standby_mode_rst'] = bool(user0 & X103_BIT_STANDBY_RST)
        out['enable_long_delay_time'] = not bool(user0 & X103_BIT_PORCTR)
    elif key == 'x035':
        out['enable_soft_ctrl_iwdg'] = bool(user0 & X103_BIT_IWDG_SW)
        out['disable_stop_mode_rst'] = bool(user0 & X103_BIT_STOP_RST)
        out['disable_standby_mode_rst'] = bool(user0 & X103_BIT_STANDBY_RST)
    elif key in {'fv20x', 'fv20x_or_compact', 'fv30x'}:
        out['enable_soft_ctrl_iwdg'] = bool(user0 & FV_BIT_IWDG_SW)
        out['disable_stop_mode_rst'] = bool(user0 & FV_BIT_STOP_RST)
        out['disable_standby_mode_rst'] = bool(user0 & FV_BIT_STANDBY_RST)
        dec, _ = _ram_maps(key)
        if dec:
            out['ramx_rom_mode'] = dec.get(user0 & FV_RAM_MASK, '')
    return out


def apply_config_fields(option_profile: str, current_cfg12: bytes, config: Any) -> WriteConfigResult:
    if len(current_cfg12) < 12:
        raise ValueError('cfg12 must be 12 bytes')
    cfg12 = bytearray(current_cfg12)
    applied_fields: list[str] = []
    preserved_fields: list[str] = []

    cfg12[4] = _parse_hex_byte(getattr(config, 'data0', ''), cfg12[4])
    cfg12[5] = _parse_hex_byte(getattr(config, 'data1', ''), cfg12[5])
    cfg12[8] = _parse_hex_byte(getattr(config, 'wrp0', ''), cfg12[8])
    cfg12[9] = _parse_hex_byte(getattr(config, 'wrp1', ''), cfg12[9])
    cfg12[10] = _parse_hex_byte(getattr(config, 'wrp2', ''), cfg12[10])
    cfg12[11] = _parse_hex_byte(getattr(config, 'wrp3', ''), cfg12[11])
    applied_fields += ['data0', 'data1', 'wrp0', 'wrp1', 'wrp2', 'wrp3']

    key = (option_profile or '').strip().lower()
    if key == 'x103':
        user0 = cfg12[2]
        if _as_bool(getattr(config, 'enable_soft_ctrl_iwdg', False)):
            user0 |= X103_BIT_IWDG_SW
        else:
            user0 &= ~X103_BIT_IWDG_SW
        if _as_bool(getattr(config, 'disable_stop_mode_rst', False)):
            user0 |= X103_BIT_STOP_RST
        else:
            user0 &= ~X103_BIT_STOP_RST
        if _as_bool(getattr(config, 'disable_standby_mode_rst', False)):
            user0 |= X103_BIT_STANDBY_RST
        else:
            user0 &= ~X103_BIT_STANDBY_RST
        if _as_bool(getattr(config, 'enable_long_delay_time', False)):
            user0 &= ~X103_BIT_PORCTR
        else:
            user0 |= X103_BIT_PORCTR
        applied_fields += ['enable_soft_ctrl_iwdg', 'disable_stop_mode_rst', 'disable_standby_mode_rst', 'enable_long_delay_time']
        cfg12[2] = user0
        preserved_fields.append('ramx_rom_mode')
    elif key == 'x035':
        user0 = cfg12[2]
        if _as_bool(getattr(config, 'enable_soft_ctrl_iwdg', False)):
            user0 |= X103_BIT_IWDG_SW
        else:
            user0 &= ~X103_BIT_IWDG_SW
        if _as_bool(getattr(config, 'disable_stop_mode_rst', False)):
            user0 |= X103_BIT_STOP_RST
        else:
            user0 &= ~X103_BIT_STOP_RST
        if _as_bool(getattr(config, 'disable_standby_mode_rst', False)):
            user0 |= X103_BIT_STANDBY_RST
        else:
            user0 &= ~X103_BIT_STANDBY_RST
        cfg12[2] = (user0 & ~X035_RST_MASK) | X035_RST_MASK
        applied_fields += ['enable_soft_ctrl_iwdg', 'disable_stop_mode_rst', 'disable_standby_mode_rst']
        preserved_fields += ['enable_long_delay_time', 'ramx_rom_mode']
    elif key in {'fv20x', 'fv20x_or_compact', 'fv30x'}:
        user0 = cfg12[2]
        if _as_bool(getattr(config, 'enable_soft_ctrl_iwdg', False)):
            user0 |= FV_BIT_IWDG_SW
        else:
            user0 &= ~FV_BIT_IWDG_SW
        if _as_bool(getattr(config, 'disable_stop_mode_rst', False)):
            user0 |= FV_BIT_STOP_RST
        else:
            user0 &= ~FV_BIT_STOP_RST
        if _as_bool(getattr(config, 'disable_standby_mode_rst', False)):
            user0 |= FV_BIT_STANDBY_RST
        else:
            user0 &= ~FV_BIT_STANDBY_RST
        applied_fields += ['enable_soft_ctrl_iwdg', 'disable_stop_mode_rst', 'disable_standby_mode_rst']

        _, enc = _ram_maps(key)
        ram_mode = str(getattr(config, 'ramx_rom_mode', '') or '').strip()
        if ram_mode and ram_mode in enc:
            user0 = (user0 & ~FV_RAM_MASK) | enc[ram_mode]
            applied_fields.append('ramx_rom_mode')
        else:
            preserved_fields.append('ramx_rom_mode')
        cfg12[2] = user0
    else:
        preserved_fields += [
            'enable_rrp',
            'clear_codeflash',
            'disable_stop_mode_rst',
            'disable_standby_mode_rst',
            'enable_soft_ctrl_iwdg',
            'enable_long_delay_time',
            'ramx_rom_mode',
        ]

    preserved_fields += ['enable_rrp', 'clear_codeflash']
    if key not in {'x103', 'x035', 'fv20x', 'fv20x_or_compact', 'fv30x'}:
        preserved_fields += ['disable_stop_mode_rst', 'disable_standby_mode_rst', 'enable_soft_ctrl_iwdg', 'enable_long_delay_time']
    elif key in {'fv20x', 'fv20x_or_compact', 'fv30x'} and 'enable_long_delay_time' not in preserved_fields:
        preserved_fields.append('enable_long_delay_time')
    seen = set()
    preserved_fields = [x for x in preserved_fields if not (x in seen or seen.add(x))]
    return WriteConfigResult(cfg12=bytes(cfg12), applied_fields=applied_fields, preserved_fields=preserved_fields)
