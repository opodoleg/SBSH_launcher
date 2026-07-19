from PyQt6.QtCore import QObject, pyqtSignal
import subprocess


class SteamCMDWorker(QObject):
    finished = pyqtSignal(int)
    output = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, steamcmd_path, appid, modid):
        super().__init__()

        self.steamcmd_path = steamcmd_path
        self.appid = str(appid)
        self.modid = str(modid)

    def run(self):
        try:
            process = subprocess.Popen(
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
                text=True
            )

            for line in process.stdout:
                self.output.emit(line.rstrip())

            process.wait()

            self.finished.emit(process.returncode)

        except Exception as e:
            self.error.emit(str(e))