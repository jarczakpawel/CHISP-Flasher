from __future__ import annotations

import time
from dataclasses import dataclass

from chisp_flasher.transport.serial_link import SerialLink


@dataclass(slots=True)
class AutoDIProfile:
    boot_is_dtr: bool
    boot_assert: bool
    reset_assert: bool


def set_lines(link: SerialLink, profile: AutoDIProfile) -> None:
    if profile.boot_is_dtr:
        link.set_control_lines(dtr=profile.boot_assert, rts=profile.reset_assert, order='dtr-rts')
    else:
        link.set_control_lines(rts=profile.boot_assert, dtr=profile.reset_assert, order='rts-dtr')


def pulse_reset(link: SerialLink, profile: AutoDIProfile) -> None:
    if profile.boot_is_dtr:
        link.set_control_lines(rts=(not profile.reset_assert))
        time.sleep(0.02)
        link.set_control_lines(rts=profile.reset_assert)
    else:
        link.set_control_lines(dtr=(not profile.reset_assert))
        time.sleep(0.02)
        link.set_control_lines(dtr=profile.reset_assert)


def candidate_profiles() -> list[AutoDIProfile]:
    out: list[AutoDIProfile] = []
    for boot_is_dtr in (True, False):
        for boot_assert in (True, False):
            for reset_assert in (True, False):
                out.append(AutoDIProfile(boot_is_dtr=boot_is_dtr, boot_assert=boot_assert, reset_assert=reset_assert))
    return out

