from ..klipper import KlipperMCU


class KQFBootloaderBase(object):
    def __init__(mcu: KlipperMCU):
        self.__mcu = mcu
        pass

    @classmethod
    def applicable(cls, KlipperMCU) -> bool:
        return False

    def priority(self):
        return 0

    def can_check_active(cls):
        return False

    def is_active(self):
        raise ValueError("Not Supported")

    def activate(self):
        raise ValueError("Not Supported")

    def is_valid(self):
        # Returns true if this bootloader is properly configured, and no required options are missing
        return False

    @classmethod
    def requries_target():
        return False
