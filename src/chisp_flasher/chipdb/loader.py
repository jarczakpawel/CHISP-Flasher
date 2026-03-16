from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import yaml


@dataclass(slots=True)
class ChipDB:
    raw: dict
    families: dict
    transport_matrix: dict
    chips: dict
    gui_profiles: dict
    option_profiles: dict


@lru_cache(maxsize=1)
def load_chipdb() -> ChipDB:
    path = files('chisp_flasher.data').joinpath('chipdb.yaml')
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    return ChipDB(
        raw=raw,
        families=dict(raw.get('backend_families') or {}),
        transport_matrix=dict(raw.get('transport_matrix') or {}),
        chips=dict(raw.get('chips') or {}),
        gui_profiles=dict(raw.get('gui_profiles') or {}),
        option_profiles=dict(raw.get('option_profiles') or {}),
    )
