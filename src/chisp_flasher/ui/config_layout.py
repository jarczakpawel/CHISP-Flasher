from __future__ import annotations

SECTION_ORDER = [
    'protection',
    'reset_watchdog',
    'memory_split',
    'memory_split_optional',
    'user_bytes',
]

SECTION_META = {
    'protection': {
        'title': 'Protection',
        'description': 'Read protection and write-protection bytes for the target flash map.',
        'fields': ['enable_rrp', 'clear_codeflash', 'wrp0', 'wrp1', 'wrp2', 'wrp3'],
    },
    'reset_watchdog': {
        'title': 'Reset and watchdog',
        'description': 'Boot-time behavior bits such as stop-mode reset, standby reset, watchdog mode and long reset delay.',
        'fields': ['disable_stop_mode_rst', 'disable_standby_mode_rst', 'enable_soft_ctrl_iwdg', 'enable_long_delay_time'],
    },
    'memory_split': {
        'title': 'Memory layout',
        'description': 'Devices in this family can remap part of flash into RAMX. Read config before changing this.',
        'fields': ['ramx_rom_mode'],
    },
    'memory_split_optional': {
        'title': 'Memory layout',
        'description': 'Only some variants expose RAMX/ROM layout switching. If the chip does not support it, this section stays hidden.',
        'fields': ['ramx_rom_mode'],
    },
    'user_bytes': {
        'title': 'User bytes',
        'description': 'Application-defined user data and raw write-protection bytes.',
        'fields': ['data0', 'data1'],
    },
}

FIELD_NOTES = {
    'enable_rrp': 'Read protection is not written automatically yet. It stays visible for roadmap completeness.',
    'clear_codeflash': 'Mass erase is handled by the flash path. This checkbox is informational until a dedicated clear/apply flow is added.',
    'enable_long_delay_time': 'For x103 this maps to the power-on reset delay bit. Read the current config first so the form starts from the real state.',
    'ramx_rom_mode': 'Changing RAMX/ROM layout changes how much flash is usable as code and how much SRAMX is exposed.',
}

PROFILE_SUMMARY = {
    'x103': 'x103 profile: classic option bytes, long reset delay bit, no RAMX/ROM split.',
    'fv20x': 'V20x profile: stop/standby reset bits, software IWDG bit and optional RAMX/ROM split.',
    'fv20x_or_compact': 'F20x compact profile: same family-level option layout, but some variants hide RAMX/ROM switching.',
    'fv30x': 'V30x profile: split-memory capable option bytes with larger RAMX/ROM layouts.',
}
