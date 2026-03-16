from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from chisp_flasher.core.errors import TransportError
from chisp_flasher.protocol.native_usb import NativeUsbFrame, parse_frame


@dataclass(slots=True)
class UsbNativeDeviceInfo:
    vid: int
    pid: int
    interface_number: int | None = None
    endpoint_out: int | None = None
    endpoint_in: int | None = None
    bus: int | None = None
    address: int | None = None
    product: str = ''
    manufacturer: str = ''
    serial_number: str = ''

    @property
    def selector(self) -> str:
        if self.bus is not None and self.address is not None:
            return f'{self.vid:04x}:{self.pid:04x}:{self.bus:02x}:{self.address:02x}'
        return f'{self.vid:04x}:{self.pid:04x}'

    @property
    def friendly_label(self) -> str:
        product = (self.product or '').strip()
        manufacturer = (self.manufacturer or '').strip()
        if product:
            return product
        if manufacturer:
            return manufacturer
        if self.pid == 0x55E0:
            return 'WCH USB bootloader'
        if self.vid == 0x1A86:
            return 'WCH USB device'
        return 'USB device'

    @property
    def display_text(self) -> str:
        label = self.friendly_label
        suffix = f' [{self.selector}]'
        if self.interface_number is not None and self.endpoint_out is not None and self.endpoint_in is not None:
            suffix += f' intf {self.interface_number} OUT 0x{self.endpoint_out:02x} IN 0x{self.endpoint_in:02x}'
        return f'{label}{suffix}'


class UsbNativeLink:
    def __init__(self, info: UsbNativeDeviceInfo, trace: bool = False, timeout_ms: int = 1000):
        self.info = info
        self.trace = bool(trace)
        self.timeout_ms = int(timeout_ms)
        self._usb_core: Any | None = None
        self._usb_util: Any | None = None
        self._dev: Any | None = None
        self._ep_out: Any | None = None
        self._ep_in: Any | None = None
        self._claimed_interface_number: int | None = None

    @staticmethod
    def _load_usb() -> tuple[Any, Any]:
        try:
            import usb.core  # type: ignore
            import usb.util  # type: ignore
        except Exception as e:
            raise TransportError('pyusb/libusb is required for native USB transport') from e
        return usb.core, usb.util

    @classmethod
    def parse_selector(
        cls,
        selector: str,
        *,
        interface_number: int | None = None,
        endpoint_out: int | None = None,
        endpoint_in: int | None = None,
    ) -> UsbNativeDeviceInfo:
        s = (selector or '').strip().lower()
        if not s:
            raise TransportError('usb device selector is empty')
        parts = s.split(':')
        if len(parts) < 2:
            raise TransportError(f'bad usb selector: {selector!r}')
        try:
            vid = int(parts[0], 16)
            pid = int(parts[1], 16)
        except Exception as e:
            raise TransportError(f'bad usb selector: {selector!r}') from e
        bus = int(parts[2], 16) if len(parts) >= 3 and parts[2] else None
        address = int(parts[3], 16) if len(parts) >= 4 and parts[3] else None
        return UsbNativeDeviceInfo(
            vid=vid,
            pid=pid,
            bus=bus,
            address=address,
            interface_number=interface_number,
            endpoint_out=endpoint_out,
            endpoint_in=endpoint_in,
        )

    @classmethod
    def list_candidate_infos(cls) -> list[UsbNativeDeviceInfo]:
        usb_core, usb_util = cls._load_usb()
        out: list[UsbNativeDeviceInfo] = []
        devices = usb_core.find(find_all=True)
        if devices is None:
            return out
        for dev in devices:
            try:
                vid = int(dev.idVendor)
                pid = int(dev.idProduct)
                info = UsbNativeDeviceInfo(
                    vid=vid,
                    pid=pid,
                    bus=getattr(dev, 'bus', None),
                    address=getattr(dev, 'address', None),
                )
                try:
                    info.product = str(usb_util.get_string(dev, dev.iProduct) or '')
                except Exception:
                    pass
                try:
                    info.manufacturer = str(usb_util.get_string(dev, dev.iManufacturer) or '')
                except Exception:
                    pass
                try:
                    info.serial_number = str(usb_util.get_string(dev, dev.iSerialNumber) or '')
                except Exception:
                    pass
                cfg = None
                try:
                    cfg = dev.get_active_configuration()
                except Exception:
                    try:
                        dev.set_configuration()
                        cfg = dev.get_active_configuration()
                    except Exception:
                        cfg = None
                if cfg is not None:
                    for intf in cfg:
                        ep_out = None
                        ep_in = None
                        for ep in intf:
                            addr = int(ep.bEndpointAddress)
                            if usb_util.endpoint_direction(addr) == usb_util.ENDPOINT_OUT and ep_out is None:
                                ep_out = addr
                            elif usb_util.endpoint_direction(addr) == usb_util.ENDPOINT_IN and ep_in is None:
                                ep_in = addr
                        out.append(UsbNativeDeviceInfo(
                            vid=info.vid,
                            pid=info.pid,
                            bus=info.bus,
                            address=info.address,
                            product=info.product,
                            manufacturer=info.manufacturer,
                            serial_number=info.serial_number,
                            interface_number=int(getattr(intf, 'bInterfaceNumber', 0)),
                            endpoint_out=ep_out,
                            endpoint_in=ep_in,
                        ))
                    continue
                out.append(info)
            except Exception:
                continue
        dedup: dict[str, UsbNativeDeviceInfo] = {}
        for item in out:
            key = item.selector
            if item.interface_number is not None:
                key += f':{item.interface_number:02x}'
            dedup[key] = item
        return sorted(dedup.values(), key=lambda x: (x.vid, x.pid, x.bus or -1, x.address or -1, x.interface_number or -1))


    def open(self) -> None:
        usb_core, usb_util = self._load_usb()
        self._usb_core = usb_core
        self._usb_util = usb_util
        kwargs = {'idVendor': self.info.vid, 'idProduct': self.info.pid}
        if self.info.bus is not None:
            kwargs['bus'] = self.info.bus
        if self.info.address is not None:
            kwargs['address'] = self.info.address
        dev = usb_core.find(**kwargs)
        if dev is None:
            raise TransportError(f'native usb device not found: {self.info.selector}')
        self._dev = dev
        cfg = None
        try:
            cfg = dev.get_active_configuration()
        except Exception:
            cfg = None
        if cfg is None:
            try:
                dev.set_configuration()
            except Exception:
                pass
            try:
                cfg = dev.get_active_configuration()
            except Exception as e:
                raise TransportError(f'native usb configuration not available: {e}') from e
        intf = None
        if self.info.interface_number is not None:
            try:
                intf = cfg[(int(self.info.interface_number), 0)]
            except Exception:
                intf = None
        if intf is None:
            candidates = []
            for candidate in cfg:
                out_ep = None
                in_ep = None
                for ep in candidate:
                    addr = int(ep.bEndpointAddress)
                    if usb_util.endpoint_direction(addr) == usb_util.ENDPOINT_OUT and out_ep is None:
                        out_ep = addr
                    elif usb_util.endpoint_direction(addr) == usb_util.ENDPOINT_IN and in_ep is None:
                        in_ep = addr
                score = 0
                if out_ep is not None and in_ep is not None:
                    score += 100
                if self.info.endpoint_out is not None and out_ep == int(self.info.endpoint_out):
                    score += 40
                if self.info.endpoint_in is not None and in_ep == int(self.info.endpoint_in):
                    score += 40
                if self.info.pid == 0x55E0 and out_ep == 0x02 and in_ep == 0x82:
                    score += 50
                if getattr(candidate, 'bInterfaceClass', None) == 0xFF:
                    score += 10
                candidates.append((score, candidate))
            if candidates:
                candidates.sort(key=lambda item: (-item[0], int(getattr(item[1], 'bInterfaceNumber', 0))))
                intf = candidates[0][1]
        if intf is None:
            raise TransportError('native usb interface not found')
        try:
            if dev.is_kernel_driver_active(int(intf.bInterfaceNumber)):
                dev.detach_kernel_driver(int(intf.bInterfaceNumber))
        except Exception:
            pass
        try:
            usb_util.claim_interface(dev, int(intf.bInterfaceNumber))
            self._claimed_interface_number = int(intf.bInterfaceNumber)
        except Exception:
            self._claimed_interface_number = None
        ep_out = None
        ep_in = None
        for ep in intf:
            addr = int(ep.bEndpointAddress)
            if self.info.endpoint_out is not None and addr == int(self.info.endpoint_out):
                ep_out = ep
            if self.info.endpoint_in is not None and addr == int(self.info.endpoint_in):
                ep_in = ep
        if ep_out is None or ep_in is None:
            for ep in intf:
                addr = int(ep.bEndpointAddress)
                if usb_util.endpoint_direction(addr) == usb_util.ENDPOINT_OUT and ep_out is None:
                    ep_out = ep
                elif usb_util.endpoint_direction(addr) == usb_util.ENDPOINT_IN and ep_in is None:
                    ep_in = ep
        if ep_out is None or ep_in is None:
            raise TransportError('native usb bulk endpoints not found')
        self.info.interface_number = int(intf.bInterfaceNumber)
        self.info.endpoint_out = int(ep_out.bEndpointAddress)
        self.info.endpoint_in = int(ep_in.bEndpointAddress)
        self._ep_out = ep_out
        self._ep_in = ep_in

    def close(self) -> None:
        if self._dev is None:
            return
        try:
            if self._usb_util is not None and self._claimed_interface_number is not None:
                self._usb_util.release_interface(self._dev, self._claimed_interface_number)
        except Exception:
            pass
        try:
            if self._usb_util is not None:
                self._usb_util.dispose_resources(self._dev)
        except Exception:
            pass
        self._claimed_interface_number = None
        self._ep_out = None
        self._ep_in = None
        self._dev = None

    def flush(self) -> None:
        if self._dev is None or self._ep_in is None:
            return
        while True:
            try:
                self._dev.read(self._ep_in.bEndpointAddress, self._ep_in.wMaxPacketSize, timeout=10)
            except Exception:
                break

    def write_frame(self, frame: bytes) -> None:
        if self._dev is None or self._ep_out is None:
            raise TransportError('native usb link is not open')
        try:
            self._dev.write(self._ep_out.bEndpointAddress, frame, timeout=self.timeout_ms)
        except Exception as e:
            raise TransportError(f'native usb write failed: {e}') from e

    def read_frame(self, timeout_ms: int = 1000) -> bytes:
        if self._dev is None or self._ep_in is None:
            raise TransportError('native usb link is not open')
        tout = int(timeout_ms)
        try:
            data = self._dev.read(self._ep_in.bEndpointAddress, self._ep_in.wMaxPacketSize, timeout=tout)
            return bytes(data)
        except Exception as e:
            raise TransportError(f'native usb read failed: {e}') from e

    def txrx(self, frame: bytes, expect_cmd: int, timeout_ms: int = 1000) -> tuple[int, bytes]:
        self.write_frame(frame)
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            if time.monotonic() >= deadline:
                raise TransportError(f'native usb timeout waiting for cmd=0x{expect_cmd:02x}')
            chunk = self.read_frame(timeout_ms=max(10, min(timeout_ms, 200)))
            parsed = parse_frame(chunk)
            if parsed.cmd != expect_cmd:
                continue
            return parsed.code, parsed.data

    def txrx_frame(self, frame: bytes, expect_cmd: int, timeout_ms: int = 1000) -> NativeUsbFrame:
        self.write_frame(frame)
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            if time.monotonic() >= deadline:
                raise TransportError(f'native usb timeout waiting for cmd=0x{expect_cmd:02x}')
            chunk = self.read_frame(timeout_ms=max(10, min(timeout_ms, 200)))
            parsed = parse_frame(chunk)
            if parsed.cmd != expect_cmd:
                continue
            return parsed
