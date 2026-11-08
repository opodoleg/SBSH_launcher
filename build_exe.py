"""
Скрипт для сборки SBSH Launcher в исполняемый файл (.exe) с помощью PyInstaller.
"""
import os
import shutil
import subprocess
import sys

# Обеспечиваем корректный вывод UTF-8 в консоли Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build():
    print("=" * 60)
    print("[*] Сборка SBSH Launcher в EXE...")
    print("=" * 60)

    project_dir = os.path.abspath(os.path.dirname(__file__))
    os.chdir(project_dir)

    python_exe = sys.executable
    spec_file = os.path.join(project_dir, "SBSH_Launcher.spec")

    if not os.path.isfile(spec_file):
        print(f"[Ошибка] Файл спецификации {spec_file} не найден!")
        return False

    cmd = [python_exe, "-m", "PyInstaller", "--clean", "-y", spec_file]
    print(f"Выполняется команда: {' '.join(cmd)}")

    result = subprocess.run(cmd)

    if result.returncode == 0:
        dist_exe = os.path.join(project_dir, "dist", "SBSH_Launcher.exe")
        root_exe = os.path.join(project_dir, "SBSH_Launcher.exe")

        # Копируем exe в корень проекта для удобного запуска рядом с .env и настройками
        if os.path.isfile(dist_exe):
            try:
                shutil.copy2(dist_exe, root_exe)
                print(f"[+] Скопирован в корень: {root_exe}")
            except Exception as e:
                print(f"[!] Не удалось скопировать в корень: {e}")

        print("\n" + "=" * 60)
        print("[+] Сборка успешно завершена!")
        print(f"📁 Исполняемый файл: {root_exe}")
        if os.path.isfile(root_exe):
            size_mb = os.path.getsize(root_exe) / (1024 * 1024)
            print(f"📦 Размер файла: {size_mb:.2f} МБ")
        print("=" * 60)
        return True
    else:
        print("\n" + "=" * 60)
        print(f"[-] Ошибка сборки. Код возврата: {result.returncode}")
        print("=" * 60)
        return False


if __name__ == "__main__":
    success = build()
    sys.exit(0 if success else 1)
