from __future__ import annotations

from dataclasses import dataclass, field

from chisp_flasher.formats.projectfmt import CHISPProject


@dataclass(slots=True)
class Session:
    project: CHISPProject = field(default_factory=CHISPProject)
    last_project_path: str = ''
