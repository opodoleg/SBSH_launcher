import subprocess

path = r"G:\SteamCMD\steamcmd.exe"

print("Запуск:", path)

process = subprocess.run(
    [
        path,
        "+quit"
    ],
    capture_output=True,
    text=True
)

print("Код:", process.returncode)
print("Вывод:")
print(process.stdout)

print("Ошибки:")
print(process.stderr)

print(process.returncode)
print(process.stdout)
print(process.stderr)