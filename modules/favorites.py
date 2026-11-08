import json
import os
from datetime import datetime


class FavoritesManager:
    """Менеджер избранных игр с постоянным сохранением в favorites.json."""

    def __init__(self, filepath: str = None):
        if filepath is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            filepath = os.path.join(base_dir, "favorites.json")
        self.filepath = filepath
        self._favorites = []
        self.load()

    def load(self):
        """Загружает список избранного из JSON-файла."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._favorites = data
                        return
            except Exception as e:
                print(f"Ошибка загрузки favorites.json: {e}")
        self._favorites = []

    def save(self):
        """Сохраняет текущий список избранного в JSON-файл."""
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self._favorites, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения favorites.json: {e}")

    def get_all(self) -> list[dict]:
        """Возвращает копию списка всех избранных игр."""
        return list(self._favorites)

    def is_favorite(self, name: str = None, appid: int | str = None) -> bool:
        """Проверяет, находится ли игра в избранном по названию или AppID."""
        name_clean = name.strip().lower() if name else ""
        appid_clean = str(appid).strip() if appid is not None else ""

        for item in self._favorites:
            if appid_clean and str(item.get("appid", "")).strip() == appid_clean:
                return True
            if name_clean and item.get("name", "").strip().lower() == name_clean:
                return True
        return False

    def add(self, name: str, appid: int | str | None = None, save_path: str = None) -> bool:
        """
        Добавляет игру в избранное.
        Возвращает True, если добавлена новая запись.
        """
        name_clean = name.strip()
        if not name_clean:
            return False

        appid_clean = int(appid) if appid is not None and str(appid).isdigit() else None

        # Проверяем, есть ли уже в списке
        for item in self._favorites:
            if (appid_clean and item.get("appid") == appid_clean) or (
                item.get("name", "").strip().lower() == name_clean.lower()
            ):
                # Обновляем appid или путь при наличии
                if appid_clean:
                    item["appid"] = appid_clean
                if save_path:
                    item["save_path"] = save_path
                self.save()
                return False

        new_entry = {
            "name": name_clean,
            "appid": appid_clean,
            "save_path": save_path or "",
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self._favorites.append(new_entry)
        self.save()
        return True

    def remove(self, name: str = None, appid: int | str = None) -> bool:
        """Удаляет игру из избранного."""
        name_clean = name.strip().lower() if name else ""
        appid_clean = str(appid).strip() if appid is not None else ""

        initial_len = len(self._favorites)
        self._favorites = [
            item
            for item in self._favorites
            if not (
                (appid_clean and str(item.get("appid", "")).strip() == appid_clean)
                or (name_clean and item.get("name", "").strip().lower() == name_clean)
            )
        ]
        if len(self._favorites) != initial_len:
            self.save()
            return True
        return False

    def toggle(self, name: str, appid: int | str | None = None) -> bool:
        """
        Переключает статус избранного: если есть — удаляет, если нет — добавляет.
        Возвращает True, если теперь в избранном, иначе False.
        """
        if self.is_favorite(name=name, appid=appid):
            self.remove(name=name, appid=appid)
            return False
        else:
            self.add(name=name, appid=appid)
            return True


# Глобальный синглтон менеджера избранного
favorites_manager = FavoritesManager()
