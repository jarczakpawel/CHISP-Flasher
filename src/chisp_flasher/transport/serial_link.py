from __future__ import annotations

import time
from typing import Any

from chisp_flasher.core.errors import TransportError
from chisp_flasher.protocol.framing import MAGIC_RSP, scan_frames
from chisp_flasher.transport.base import TransportBase


class SerialLink(TransportBase):
    def __init__(self, port: str, baud: int = 115200, parity: str = 'N', trace: bool = False):
        self.port = port
        self.baud = int(baud)
        self.parity = str(parity).upper()
        self.trace = bool(trace)
        self.ser: Any | None = None
        self._rx = bytearray()

    def open(self) -> None:
        try:
            import serial  # type: ignore
        except Exception as e:
            raise TransportError('pyserial is required for serial transport') from e
        parity_map = {
            'N': serial.PARITY_NONE,
            'E': serial.PARITY_EVEN,
            'O': serial.PARITY_ODD,
        }
        if self.parity not in parity_map:
            raise TransportError(f'bad parity: {self.parity}')
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            timeout=0,
            write_timeout=1.0,
            rtscts=False,
            dsrdtr=False,
            bytesize=serial.EIGHTBITS,
            parity=parity_map[self.parity],
            stopbits=serial.STOPBITS_ONE,
        )

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.dtr = True
                self.ser.rts = True
            except Exception:
                pass
            self.ser.close()
        self.ser = None
        self._rx.clear()

    def ensure_open(self):
        if self.ser is None:
            raise TransportError('serial link is not open')
        return self.ser

    def set_baud(self, baud: int) -> None:
        ser = self.ensure_open()
        self.baud = int(baud)
        ser.baudrate = self.baud

    def flush(self) -> None:
        ser = self.ensure_open()
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        self._rx.clear()

    def _read_available(self) -> None:
        ser = self.ensure_open()
        n = ser.in_waiting
        if n:
            self._rx += ser.read(n)

    def recv(self, expect_cmd: int, timeout_s: float):
        end = time.monotonic() + timeout_s
        while True:
            if time.monotonic() >= end:
                raise TransportError(f'timeout waiting for cmd=0x{expect_cmd:02x}')
            self._read_available()
            frames, tail = scan_frames(bytes(self._rx), expected_magic=MAGIC_RSP)
            self._rx[:] = tail
            for frame in frames:
                if frame.cmd != expect_cmd:
                    continue
                if self.trace:
                    print(f'RX cmd=0x{frame.cmd:02x} code=0x{frame.code:02x} len={frame.length} data={frame.data.hex()}')
                return frame.code, frame.data
            time.sleep(0.002)

    def tx(self, packet: bytes) -> None:
        ser = self.ensure_open()
        if self.trace:
            print(f'TX {packet.hex()}')
        ser.write(packet)

    def txrx(self, packet: bytes, expect_cmd: int, timeout_s: float):
        self.tx(packet)
        return self.recv(expect_cmd=expect_cmd, timeout_s=timeout_s)

    def set_control_lines(self, *, dtr: bool | None = None, rts: bool | None = None, order: str = 'dtr-rts') -> None:
        ser = self.ensure_open()
        if order == 'rts-dtr':
            if rts is not None:
                ser.rts = bool(rts)
            if dtr is not None:
                ser.dtr = bool(dtr)
            return
        if dtr is not None:
            ser.dtr = bool(dtr)
        if rts is not None:
            ser.rts = bool(rts)
