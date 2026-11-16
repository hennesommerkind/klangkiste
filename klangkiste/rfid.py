"""RFID-Leser MFRC522 (RC522) über SPI – eigener, schlanker Treiber.

Braucht nur `spidev` und einen Reset-Pin (über gpiozero). Die Karten-ID ist
die 4-Byte-UID als Hex. Zusätzlich werden die Schreibweisen der alten
Phoniebox berechnet, damit importierte Karten weiter funktionieren.
"""
import logging
import threading
import time

log = logging.getLogger("rfid")

# Register
COMMAND, COM_IEN, COM_IRQ, ERROR, FIFO_DATA, FIFO_LEVEL = 0x01, 0x02, 0x04, 0x06, 0x09, 0x0A
CONTROL, BIT_FRAMING, MODE, TX_CONTROL, TX_ASK = 0x0C, 0x0D, 0x11, 0x14, 0x15
RF_CFG, T_MODE, T_PRESCALER, T_RELOAD_H, T_RELOAD_L, VERSION = 0x26, 0x2A, 0x2B, 0x2C, 0x2D, 0x37
CMD_IDLE, CMD_TRANSCEIVE, CMD_SOFTRESET = 0x00, 0x0C, 0x0F


def card_ids(uid5):
    """Liefert (Haupt-ID, [alternative Schreibweisen]) für eine gelesene UID."""
    b = list(uid5)
    main = "".join(f"{x:02X}" for x in b[:4])
    alts = [
        "".join(str(x) for x in b),            # Phoniebox 2.x mit pirc522
        "".join(str(x) for x in b[:4]),
        str(int.from_bytes(bytes(b), "big")),   # SimpleMFRC522
        str(int.from_bytes(bytes(b[:4]), "big")),
        str(int.from_bytes(bytes(b[:4]), "little")),
        str(int.from_bytes(bytes(b[:4]), "little")).zfill(10),  # USB-Leser
        main.lower(),
    ]
    return main, [a for a in dict.fromkeys(alts) if a != main]


class MFRC522:
    def __init__(self, bus=0, device=0, rst_pin=25, speed=1_000_000):
        import spidev
        from gpiozero import OutputDevice

        self._rst = OutputDevice(rst_pin, initial_value=True) if rst_pin is not None else None
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = int(speed)
        self.spi.mode = 0
        self.init()

    def _w(self, reg, val):
        self.spi.xfer2([(reg << 1) & 0x7E, val])

    def _r(self, reg):
        return self.spi.xfer2([((reg << 1) & 0x7E) | 0x80, 0])[1]

    def _set(self, reg, mask):
        self._w(reg, self._r(reg) | mask)

    def _clear(self, reg, mask):
        self._w(reg, self._r(reg) & ~mask & 0xFF)

    def init(self):
        if self._rst:
            self._rst.off()
            time.sleep(0.05)
            self._rst.on()
            time.sleep(0.05)
        self._w(COMMAND, CMD_SOFTRESET)
        time.sleep(0.05)
        self._w(T_MODE, 0x8D)
        self._w(T_PRESCALER, 0x3E)
        self._w(T_RELOAD_L, 30)
        self._w(T_RELOAD_H, 0)
        self._w(TX_ASK, 0x40)
        self._w(MODE, 0x3D)
        self._w(RF_CFG, 0x70)  # maximale Empfangsverstärkung (Gehäusewand)
        self._set(TX_CONTROL, 0x03)  # Antenne an

    def version(self):
        return self._r(VERSION)

    def _transceive(self, data):
        self._w(COM_IEN, 0x77 | 0x80)
        self._clear(COM_IRQ, 0x80)
        self._set(FIFO_LEVEL, 0x80)
        self._w(COMMAND, CMD_IDLE)
        for d in data:
            self._w(FIFO_DATA, d)
        self._w(COMMAND, CMD_TRANSCEIVE)
        self._set(BIT_FRAMING, 0x80)
        deadline = time.monotonic() + 0.05
        n = 0
        while time.monotonic() < deadline:
            n = self._r(COM_IRQ)
            if n & 0x31:
                break
        self._clear(BIT_FRAMING, 0x80)
        if not (n & 0x30) or (n & 0x01) or (self._r(ERROR) & 0x1B):
            return None, 0
        count = self._r(FIFO_LEVEL)
        last_bits = self._r(CONTROL) & 0x07
        bits = (count - 1) * 8 + last_bits if last_bits else count * 8
        return [self._r(FIFO_DATA) for _ in range(min(count, 16))], bits

    def read_uid(self):
        """Gibt die 5 Bytes (UID + Prüfbyte) zurück oder None."""
        self._w(BIT_FRAMING, 0x07)
        data, bits = self._transceive([0x26])
        if data is None or bits != 16:
            return None
        self._w(BIT_FRAMING, 0x00)
        data, _ = self._transceive([0x93, 0x20])
        if not data or len(data) != 5:
            return None
        if data[0] ^ data[1] ^ data[2] ^ data[3] != data[4]:
            return None
        return data


class MockReader:
    """Simulierter Leser für Tests ohne Hardware."""

    def __init__(self):
        self.uid = None

    def read_uid(self):
        return self.uid

    def version(self):
        return 0x92

    def init(self):
        pass


class CardWatcher:
    """Fragt den Leser ab und meldet Auflegen und Wegnehmen von Karten."""

    MISSES = 5  # so viele Fehlversuche in Folge = Karte ist weg

    def __init__(self, reader, on_card, on_remove, interval=0.12):
        self.reader = reader
        self.on_card, self.on_remove = on_card, on_remove
        self.interval = interval
        self.present = None
        self._misses = 0
        self._stop = threading.Event()
        self.ok = True

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="rfid").start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        errors = 0
        while not self._stop.wait(self.interval):
            try:
                uid = self.reader.read_uid()
                errors, self.ok = 0, True
            except Exception as e:
                errors += 1
                self.ok = False
                if errors in (1, 50):
                    log.warning("Leser-Fehler: %s", e)
                time.sleep(min(5, errors * 0.2))
                if errors % 20 == 0:
                    try:
                        self.reader.init()
                    except Exception:
                        pass
                continue
            if uid:
                self._misses = 0
                key = tuple(uid)
                if key != self.present:
                    self.present = key
                    self._fire(self.on_card, *card_ids(uid))
            elif self.present is not None:
                self._misses += 1
                if self._misses >= self.MISSES:
                    self.present = None
                    self._fire(self.on_remove)

    @staticmethod
    def _fire(fn, *args):
        try:
            fn(*args)
        except Exception:
            log.exception("Karten-Ereignis")
