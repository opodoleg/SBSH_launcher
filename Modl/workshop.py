import os
import subprocess


def check_steamcmd(steamcmd_path: str) -> dict:
    """
    Проверяет доступность и работоспособность SteamCMD.
    Возвращает словарь с результатом выполнения: success, output, error.
    """
    if not steamcmd_path or not os.path.isfile(steamcmd_path):
        return {
            "success": False,
            "error": f"Файл SteamCMD не найден: {steamcmd_path}"
        }

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.run(
            [steamcmd_path, "+quit"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=creationflags
        )
        return {
            "success": process.returncode == 0,
            "returncode": process.returncode,
            "stdout": process.stdout.strip(),
            "stderr": process.stderr.strip()
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    test_path = r"G:\SteamCMD\steamcmd.exe"
    print(f"Проверка SteamCMD по пути: {test_path}")
    result = check_steamcmd(test_path)
    if result["success"]:
        print("[Успех] SteamCMD работает исправно!")
    else:
        print(f"[Ошибка] {result.get('error') or result.get('stderr')}")

