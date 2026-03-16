from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProtocolVariant:
    key: str
    title: str


UART_FRAMED = ProtocolVariant('uart_framed', 'UART framed ISP')
LEGACY_UART_NATIVE_WRAPPED = ProtocolVariant('legacy_uart_native_wrapped', 'Legacy UART ISP over native payloads')
USB_NATIVE_PLAIN = ProtocolVariant('usb_native_plain', 'Native USB bootloader')
