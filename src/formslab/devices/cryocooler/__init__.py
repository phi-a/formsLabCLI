"""Cryocooler control board, behind a Raspberry Pi Pico I2C bridge.

    pico_board_control.py  MicroPython firmware that runs ON the Pico (package
                           data; never imported on the PC)
    pico_i2c.py            PC-side transport to that firmware
    registers.py           register map and every encoding (pure)
    board.py               `CryoBoard`: the board's behaviour and state
    config.py              operating policy: supply channel, setpoints, voltage band
    owner.py               what rCryoBoard needs: bring up the supply, own the board

See docs/CRYOCOOLER.md.
"""
from .board import CryoBoard

__all__ = ["CryoBoard"]
