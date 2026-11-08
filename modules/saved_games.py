import json
import os
from datetime import datetime


DEFAULT_POPULAR_GAMES = [
    {"name": "Garry's Mod", "appid": 4000},
    {"name": "RimWorld", "appid": 294100},
    {"name": "Project Zomboid", "appid": 108600},
    {"name": "Left 4 Dead 2", "appid": 550},
    {"name": "Hearts of Iron IV", "appid": 394360},
    {"name": "People Playground", "appid": 1118200},
    {"name": "Cities: Skylines", "appid": 255710},
    {"name": "Teardown", "appid": 1167630},
    {"name": "Arma 3", "appid": 107410},
    {"name": "Terraria", "appid": 105600},
]


class SavedGamesManager:
    """Менеджер сохранённых игр для быстрого доступа к модам Steam Workshop."""

    def __init__(self, filepath: str = None):
        if filepath is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            filepath = os.path.join(base_dir, "saved_games.json")
        self.filepath = filepath
        self._games = []
        self.load()

    def load(self):
        """Загружает список сохранённых игр из JSON-файла."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._games = data
                        return
            except Exception as e:
                print(f"Ошибка чтения saved_games.json: {e}")

        # Если файла нет — создаем с набором популярных игр с поддержкой Workshop
        self._games = list(DEFAULT_POPULAR_GAMES)
        self.save()

    def save(self):
        """Сохраняет текущий список игр в JSON-файл."""
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self._games, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка записи saved_games.json: {e}")

    def get_all(self) -> list[dict]:
        """Возвращает список всех сохранённых игр."""
        return list(self._games)

    def is_saved(self, appid: int | str = None, name: str = None) -> bool:
        """Проверяет, сохранена ли игра по AppID или названию."""
        appid_val = int(appid) if appid is not None and str(appid).isdigit() else None
        name_clean = name.strip().lower() if name else ""

        for g in self._games:
            if appid_val is not None and g.get("appid") == appid_val:
                return True
            if name_clean and g.get("name", "").strip().lower() == name_clean:
                return True
        return False

    def add(self, name: str, appid: int | str) -> bool:
        """Добавляет игру в список сохранённых игр."""
        name_clean = name.strip()
        if not name_clean:
            return False

        try:
            appid_val = int(appid)
        except (ValueError, TypeError):
            return False

        # Проверка на дубликаты
        for g in self._games:
            if g.get("appid") == appid_val:
                g["name"] = name_clean  # Обновляем название
                self.save()
                return False

        new_entry = {
            "name": name_clean,
            "appid": appid_val,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        self._games.insert(0, new_entry)
        self.save()
        return True

    def remove(self, appid: int | str = None, name: str = None) -> bool:
        """Удаляет игру из сохранённых."""
        appid_val = int(appid) if appid is not None and str(appid).isdigit() else None
        name_clean = name.strip().lower() if name else ""

        initial_len = len(self._games)
        self._games = [
            g for g in self._games
            if not (
                (appid_val is not None and g.get("appid") == appid_val)
                or (name_clean and g.get("name", "").strip().lower() == name_clean)
            )
        ]
        if len(self._games) != initial_len:
            self.save()
            return True
        return False


saved_games_manager = SavedGamesManager()
