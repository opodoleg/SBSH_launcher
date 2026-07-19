print("START")

import subprocess

print("IMPORT OK")

path = r"G:\SteamCMD\steamcmd.exe"

print("PATH:", path)

subprocess.run(
    [
        path,
        "+quit"
    ]
)

print("END")