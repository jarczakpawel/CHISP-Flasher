from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SerialPortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    manufacturer: str = ''
    product: str = ''
    hwid: str = ''

    @property
    def selector(self) -> str:
        return self.device

    @property
    def vid_pid_hex(self) -> str:
        if self.vid is None or self.pid is None:
            return '----:----'
        return f'{self.vid:04x}:{self.pid:04x}'

    @property
    def score_tags(self) -> list[str]:
        text = ' '.join([
            self.description,
            self.manufacturer,
            self.product,
            self.hwid,
            self.vid_pid_hex,
        ]).lower()
        tags: list[str] = []
        if any(x in text for x in ['1a86', 'ch340', 'ch341', 'ch9102', 'ch343', 'wch']):
            tags.append('wch-usb-uart')
        if any(x in text for x in ['10c4', 'cp210', 'silabs']):
            tags.append('cp210x')
        if any(x in text for x in ['0403', 'ftdi', 'ft232']):
            tags.append('ftdi')
        if any(x in text for x in ['067b', 'pl2303', 'prolific']):
            tags.append('pl2303')
        if any(x in text for x in ['ttyusb', 'ttyacm', 'usb serial', 'usb-serial']):
            tags.append('usb-serial')
        return tags

    @property
    def display_text(self) -> str:
        base = self.device or '-'
        desc = self.description or self.product or self.manufacturer or 'Serial device'
        if self.vid is not None and self.pid is not None:
            return f'{base} - {desc} ({self.vid:04x}:{self.pid:04x})'
        return f'{base} - {desc}'


def _list_ports_module():
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return None
    return list_ports


def list_all_ports() -> list[SerialPortInfo]:
    mod = _list_ports_module()
    if mod is None:
        return []
    out: list[SerialPortInfo] = []
    for p in mod.comports():
        out.append(SerialPortInfo(
            device=str(p.device or ''),
            description=str(p.description or ''),
            vid=None if p.vid is None else int(p.vid),
            pid=None if p.pid is None else int(p.pid),
            manufacturer=str(getattr(p, 'manufacturer', '') or ''),
            product=str(getattr(p, 'product', '') or ''),
            hwid=str(getattr(p, 'hwid', '') or ''),
        ))
    return out


def list_matching_ports(vid: int, pid: int) -> list[SerialPortInfo]:
    out: list[SerialPortInfo] = []
    for p in list_all_ports():
        if p.vid is None or p.pid is None:
            continue
        if int(p.vid) == int(vid) and int(p.pid) == int(pid):
            out.append(p)
    return out


def auto_pick_port(vid: int, pid: int) -> str | None:
    ports = list_matching_ports(vid, pid)
    if ports:
        return ports[0].device
    return None
