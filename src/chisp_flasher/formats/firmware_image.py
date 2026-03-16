from __future__ import annotations

from pathlib import Path

from chisp_flasher.core.errors import BackendError

_FLASH_BASE_ALIASES = (0x00000000, 0x08000000)


def load_firmware_image(path: str, *, chip_name: str = '', max_size: int = 0) -> bytes:
    src = Path(path)
    if not src.is_file():
        raise BackendError(f'firmware file not found: {path}')
    suffix = src.suffix.lower()
    if suffix in {'.bin'}:
        data = src.read_bytes()
    elif suffix in {'.hex', '.ihex'}:
        data = _load_hex(src.read_text(encoding='utf-8', errors='ignore'), chip_name=chip_name, max_size=max_size)
    elif suffix in {'.srec', '.s19', '.s28', '.s37', '.mot'}:
        data = _load_srec(src.read_text(encoding='utf-8', errors='ignore'), chip_name=chip_name, max_size=max_size)
    elif suffix in {'.elf'}:
        data = _load_elf(src, chip_name=chip_name, max_size=max_size)
    else:
        data = src.read_bytes()
    if max_size > 0 and len(data) > max_size:
        raise BackendError(f'firmware image is too large for {chip_name or "target"}: {len(data)} bytes > {max_size} bytes')
    return data


def _normalize_ranges(ranges: list[tuple[int, bytes]], *, chip_name: str, max_size: int) -> bytes:
    if not ranges:
        raise BackendError('firmware image is empty')
    min_addr = min(addr for addr, _data in ranges)
    max_addr = max(addr + len(_data) for addr, _data in ranges)
    if min_addr < 0:
        raise BackendError('firmware image contains negative address')
    base = 0
    if min_addr >= 0x08000000:
        base = 0x08000000
    rel_min = min_addr - base
    rel_max = max_addr - base
    if rel_min < 0:
        raise BackendError('firmware image base address is not supported')
    if max_size > 0 and rel_max > max_size:
        raise BackendError(f'firmware image address range exceeds flash for {chip_name or "target"}: 0x{rel_max:08X} > 0x{max_size:08X}')
    out = bytearray(b'\xff' * rel_max)
    for addr, data in ranges:
        start = addr - base
        end = start + len(data)
        for i, b in enumerate(data):
            pos = start + i
            cur = out[pos]
            if cur != 0xFF and cur != b:
                raise BackendError(f'firmware image has overlapping data at 0x{pos:08X}')
            out[pos] = b
    while out and out[-1] == 0xFF:
        out.pop()
    if not out:
        raise BackendError('firmware image is empty')
    return bytes(out)


def _parse_hex_bytepair(text: str) -> int:
    try:
        return int(text, 16)
    except Exception as exc:
        raise BackendError(f'invalid hex byte: {text}') from exc


def _load_hex(text: str, *, chip_name: str, max_size: int) -> bytes:
    upper = 0
    start_linear = None
    start_segment = None
    ranges: list[tuple[int, bytes]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(':'):
            raise BackendError('invalid Intel HEX line')
        payload = line[1:]
        if len(payload) < 10 or (len(payload) & 1):
            raise BackendError('invalid Intel HEX length')
        rec = bytes(_parse_hex_bytepair(payload[i:i + 2]) for i in range(0, len(payload), 2))
        count = rec[0]
        addr = (rec[1] << 8) | rec[2]
        rtype = rec[3]
        data = rec[4:4 + count]
        csum = rec[4 + count]
        if ((sum(rec[:-1]) + csum) & 0xFF) != 0:
            raise BackendError('invalid Intel HEX checksum')
        if rtype == 0x00:
            ranges.append((upper + addr, data))
        elif rtype == 0x01:
            break
        elif rtype == 0x02:
            if len(data) != 2:
                raise BackendError('invalid Intel HEX segment address record')
            upper = (((data[0] << 8) | data[1]) << 4)
            start_segment = upper
        elif rtype == 0x04:
            if len(data) != 2:
                raise BackendError('invalid Intel HEX linear address record')
            upper = (((data[0] << 8) | data[1]) << 16)
            start_linear = upper
        elif rtype in {0x03, 0x05}:
            continue
        else:
            raise BackendError(f'unsupported Intel HEX record type: 0x{rtype:02X}')
    _ = start_linear, start_segment
    return _normalize_ranges(ranges, chip_name=chip_name, max_size=max_size)


def _load_srec(text: str, *, chip_name: str, max_size: int) -> bytes:
    ranges: list[tuple[int, bytes]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(line) < 4 or line[0] != 'S':
            raise BackendError('invalid S-Record line')
        rectype = line[1]
        count = _parse_hex_bytepair(line[2:4])
        body_hex = line[4:]
        if len(body_hex) != count * 2:
            raise BackendError('invalid S-Record length')
        body = bytes(_parse_hex_bytepair(body_hex[i:i + 2]) for i in range(0, len(body_hex), 2))
        if (sum([count, *body]) & 0xFF) != 0xFF:
            raise BackendError('invalid S-Record checksum')
        if rectype in {'0', '5', '6', '7', '8', '9'}:
            continue
        if rectype == '1':
            addr_len = 2
        elif rectype == '2':
            addr_len = 3
        elif rectype == '3':
            addr_len = 4
        else:
            raise BackendError(f'unsupported S-Record type: S{rectype}')
        addr = int.from_bytes(body[:addr_len], 'big')
        data = body[addr_len:-1]
        ranges.append((addr, data))
    return _normalize_ranges(ranges, chip_name=chip_name, max_size=max_size)


def _load_elf(path: Path, *, chip_name: str, max_size: int) -> bytes:
    try:
        from elftools.elf.elffile import ELFFile  # type: ignore
    except Exception as exc:
        raise BackendError('ELF support requires pyelftools') from exc
    ranges: list[tuple[int, bytes]] = []
    with path.open('rb') as fh:
        elf = ELFFile(fh)
        for seg in elf.iter_segments():
            if seg['p_type'] != 'PT_LOAD':
                continue
            data = seg.data()
            if not data:
                continue
            addr = int(seg['p_paddr'] or seg['p_vaddr'] or 0)
            memsz = int(seg['p_memsz'])
            filesz = int(seg['p_filesz'])
            if memsz < filesz:
                raise BackendError('ELF segment has memsz < filesz')
            if memsz > filesz:
                data = data + (b'\x00' * (memsz - filesz))
            ranges.append((addr, data))
    return _normalize_ranges(ranges, chip_name=chip_name, max_size=max_size)
