from __future__ import annotations

GUIDES = {
    'USB-UART Auto DI': {
        'title': 'USB-UART Auto DI',
        'summary': 'Use this when a USB-UART bridge is wired to BOOT and RESET through DTR/RTS so the app can enter the ROM bootloader automatically.',
        'steps': [
            'Connect the USB-UART adapter to RX, TX, 3V3 and GND and make sure DTR/RTS are wired to BOOT and RESET.',
            'Pick the serial port of the bridge and keep USB-UART Auto DI enabled.',
            'Run Detect target. The tool will toggle DTR/RTS and probe the ROM bootloader automatically.',
        ],
        'details': 'This is still the serial bootloader path. It is not the native USB ISP device.',
    },
    'Serial bootloader': {
        'title': 'Manual serial bootloader',
        'summary': 'Use this when the target exposes a UART ISP path and you are entering the bootloader with BOOT and RESET.',
        'steps': [
            'Connect RX, TX, 3V3 and GND to the target or plug in the USB-UART adapter.',
            'For manual entry hold BOOT and pulse RESET. For boards with DTR/RTS wiring enable Auto DI.',
            'Pick the serial port and fast baud, then run Detect target.',
        ],
        'details': 'Read config first so the form starts from the real option-byte state. Auto DI is optional and only applies to the serial path.',
    },
    'Native USB bootloader': {
        'title': 'Native USB ROM bootloader',
        'summary': 'Use this when the target enumerates directly as the WCH USB ISP device.',
        'steps': [
            'Put the MCU into ROM bootloader mode so it appears as the USB ISP device.',
            'Pick the correct USB device selector.',
            'Run Detect target to confirm interface and endpoints before flashing.',
        ],
        'details': 'No serial bridge is involved here. Interface and endpoint values can be auto-filled from detection.',
    },
    'USB bootloader': {
        'title': 'USB bootloader',
        'summary': 'Use this when the target is reached over USB but not through the native plain-frame CH32V30x path.',
        'steps': [
            'Pick the USB-exposed device or bridge.',
            'Run Detect target and verify the reported path.',
            'Read config before applying changes.',
        ],
        'details': 'This mode is a generic USB entry kept for families that are not yet split into more specific UX flows.',
    },
}


def get_guide(display_mode: str) -> dict:
    return GUIDES.get(display_mode) or GUIDES['USB bootloader']
