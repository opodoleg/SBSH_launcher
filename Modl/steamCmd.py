import os
import subprocess
from PyQt6.QtCore import QObject, pyqtSignal


class SteamCMDWorker(QObject):
    finished = pyqtSignal(int)
    output = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, steamcmd_path: str, appid: int | str, modid: int | str):
        super().__init__()
        self.steamcmd_path = steamcmd_path
        self.appid = str(appid)
        self.modid = str(modid)
        self._process = None
        self._is_cancelled = False

    def run(self):
        """Запускает загрузку мода через SteamCMD."""
        try:
            if not self.steamcmd_path or not os.path.isfile(self.steamcmd_path):
                raise FileNotFoundError(
                    f"Исполняемый файл SteamCMD не найден: '{self.steamcmd_path}'"
                )

            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

            self._process = subprocess.Popen(
                [
                    self.steamcmd_path,
                    "+login", "anonymous",
                    "+workshop_download_item",
                    self.appid,
                    self.modid,
                    "+quit"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags
            )

            if self._process.stdout:
                for line in self._process.stdout:
                    if self._is_cancelled:
                        break
                    self.output.emit(line.rstrip())

            self._process.wait()
            returncode = self._process.returncode if not self._is_cancelled else -1
            self.finished.emit(returncode)

        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(-1)
        finally:
            self._process = None

    def cancel(self):
        """Безопасно прерывает процесс скачивания."""
        self._is_cancelled = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
