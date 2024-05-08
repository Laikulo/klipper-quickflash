from enum import Enum
from typing import Optional

from .base import KQFBootloaderBase
from ..klipper import KlipperMCU


class BootloaderKatapult(KQFBootloaderBase):

    def __init__(self, mcu: KlipperMCU):
        super(self, mcu)
        self._mode: Optional["KatapultMode"] = None

    @classmethod
    def applicable(cls, mcu: KlipperMCU):
        # Katapult is only applicable if explicitly enabled
        return mcu.bootloader == "katapult"

    def priority(self):
        # If katapult is enabled, it is the highest priority
        return 99999

    def is_valid(self):
        if self._mode is None:
            return False

    def can_check_active():
        return self._mode in [
            KatapultMode.CANBUS,
            KatapultMode.USB_CDC,
            KatapultMode.USB_CAN,
        ]
        # Katapult can check activity in any mode except "real" serial

    def can_activate():
        # Katapult can activate on all parts supported by katapult
        return


def KatapultMode(Enum):
    # Katapult attached via CAN, and programmed by CAN. Usually a real canbus, or a downstream of a canbridge
    CANBUS,
    # Klipper's canbridge mode. Activate by CAN, flash by USB_CDC.
    USB_CAN,
    # USB "Serial" mode for both activation and delivery
    USB_CDC,
    # Non-USB serial, includes tty and real rs232. Also may include some older arduinos that use a descrete usb->serial chip.
    SERIAL
