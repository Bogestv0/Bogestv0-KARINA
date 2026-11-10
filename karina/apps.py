"""Поиск и запуск программ: ваши (config), Bogestv0, системные, и всё из меню «Пуск»."""
from __future__ import annotations
import os, threading, time, webbrowser
from pathlib import Path
from rapidfuzz import fuzz
from .textutil import norm, translit
from . import winutil

# Программы Bogestv0. Запускаются через ярлык из меню «Пуск», который создаёт установщик;
# если стоит портативная версия — укажите путь на вкладке «Программы».
BUILTIN = [
    {"name": "Bogestv0 MIXER", "aliases": ["миксер", "микшер", "mixer", "аудио микшер"], "find": "bogestv0 mixer", "proc": ["bogestv0-mixer", "Bogestv0 MIXER"]},
    {"name": "Bogestv0 REMOTE", "aliases": ["ремоут", "ремот", "remote", "удаленный доступ", "удаленка"], "find": "bogestv0 remote", "proc": ["Bogestv0 REMOTE"]},
    {"name": "Bogestv0 Unblock", "aliases": ["анблок", "unblock", "обход", "обход блокировок"], "find": "bogestv0 unblock", "proc": ["Bogestv0-Unblock", "Bogestv0 Unblock"]},
    {"name": "Bogestv0 VPN", "aliases": ["впн", "vpn"], "find": "bogestv0 vpn", "proc": []},
]

SYSTEM = [
    {"name": "Блокнот", "aliases": ["блокнот", "notepad"], "exec": "notepad.exe", "proc": ["notepad"]},
    {"name": "Калькулятор", "aliases": ["калькулятор", "calculator"], "exec": "calc.exe", "proc": ["CalculatorApp", "calc"]},
    {"name": "Проводник", "aliases": ["проводник", "файлы", "мои файлы", "explorer"], "exec": "explorer.exe", "proc": []},
    {"name": "Диспетчер задач", "aliases": ["диспетчер задач", "диспетчер", "task manager"], "exec": "taskmgr.exe", "proc": ["Taskmgr"]},
    {"name": "Paint", "aliases": ["пейнт", "paint", "рисовалка"], "exec": "mspaint.exe", "proc": ["mspaint"]},
    {"name": "Параметры Windows", "aliases": ["параметры", "настройки windows", "настройки виндовс"], "exec": "ms-settings:", "proc": []},
    {"name": "Панель управления", "aliases": ["панель управления"], "exec": "control.exe", "proc": []},
    {"name": "Командная строка", "aliases": ["командная строка", "терминал", "консоль"], "exec": "cmd.exe", "proc": ["cmd"]},
    {"name": "Браузер", "aliases": ["браузер", "интернет"], "url": "about:blank", "browser": True, "proc": []},
    {"name": "Google Chrome", "aliases": ["хром", "гугл хром", "chrome"], "find": "google chrome", "exec_alt": "chrome.exe", "proc": ["chrome"]},
    {"name": "Microsoft Edge", "aliases": ["эдж", "edge"], "exec": "msedge.exe", "proc": ["msedge"]},
    {"name": "Firefox", "aliases": ["фаерфокс", "файрфокс", "firefox"], "find": "firefox", "proc": ["firefox"]},
    {"name": "Discord", "aliases": ["дискорд", "discord"], "find": "discord", "proc": ["Discord"]},
    {"name": "Telegram", "aliases": ["телеграм", "телега", "telegram"], "find": "telegram", "proc": ["Telegram"]},
    {"name": "Steam", "aliases": ["стим", "steam"], "find": "steam", "proc": ["steam"]},
    {"name": "Spotify", "aliases": ["спотифай", "spotify"], "find": "spotify", "proc": ["Spotify"]},
    {"name": "Word", "aliases": ["ворд", "word"], "find": "word", "proc": ["WINWORD"]},
    {"name": "Excel", "aliases": ["эксель", "excel"], "find": "excel", "proc": ["EXCEL"]},
    {"name": "Visual Studio Code", "aliases": ["вс код", "vs code", "код"], "find": "visual studio code", "proc": ["Code"]},
]

SITES = {
    "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com",
    "вк": "https://vk.com", "вконтакте": "https://vk.com",
    "гитхаб": "https://github.com", "github": "https://github.com",
    "яндекс": "https://ya.ru", "гугл": "https://www.google.com", "google": "https://www.google.com",
    "почта": "https://mail.google.com", "джимейл": "https://mail.google.com",
    "твич": "https://www.twitch.tv", "twitch": "https://www.twitch.tv",
    "википедия": "https://ru.wikipedia.org", "карты": "https://yandex.ru/maps",
    "переводчик": "https://translate.google.com",
}


def _stem(s: str) -> str:
    """Грубо отбрасывает падежные окончания: «мою прогу» ≈ «моя прога»."""
    return " ".join(w[:max(3, len(w) - 2)] for w in s.split())


class AppIndex:
    def __init__(self, cfg):
        self.cfg = cfg
        self._menu: list[tuple[str, str]] = []  # (название, путь к ярлыку)
        self._menu_ts = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self.refresh_menu, daemon=True).start()

    # ---------- меню «Пуск» ----------
    def refresh_menu(self):
        dirs = [os.environ.get("ProgramData"), os.environ.get("APPDATA")]
        items: list[tuple[str, str]] = []
        for base in dirs:
            if not base:
                continue
            root = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if root.exists():
                for p in root.rglob("*.lnk"):
                    n = p.stem
                    if any(w in n.lower() for w in ("uninstall", "удалить", "удаление", "readme", "справка")):
                        continue
                    items.append((n, str(p)))
        with self._lock:
            self._menu, self._menu_ts = items, time.time()

    def _menu_items(self):
        if time.time() - self._menu_ts > 600:
            threading.Thread(target=self.refresh_menu, daemon=True).start()
        with self._lock:
            return list(self._menu)

    # ---------- каталог ----------
    def entries(self) -> list[dict]:
        return [dict(e, src="user") for e in self.cfg.get("apps", [])] + \
               [dict(e, src="bogestv0") for e in BUILTIN] + [dict(e, src="system") for e in SYSTEM]

    def find_shortcut(self, query: str, threshold: int = 85) -> str | None:
        q = norm(query)
        best, best_s = None, 0
        for name, path in self._menu_items():
            s = max(fuzz.partial_ratio(q, norm(name)), fuzz.ratio(translit(q), translit(norm(name))))
            if norm(name).startswith(q):
                s = max(s, 96)
            if s > best_s:
                best, best_s = path, s
        return best if best_s >= threshold else None

    def resolve(self, spoken: str) -> dict | None:
        """Лучшее совпадение по имени из речи. Возвращает запись программы или None."""
        q = norm(spoken)
        if not q:
            return None
        best, best_s = None, 0.0
        for rank, e in enumerate(self.entries()):
            bonus = 3 if e["src"] == "user" else 2 if e["src"] == "bogestv0" else 1 if e["src"] == "system" else 0
            for a in [e["name"], *e.get("aliases", [])]:
                a = norm(a)
                if not a:
                    continue
                s = 100.0 if a == q else max(fuzz.WRatio(q, a), fuzz.ratio(translit(q), translit(a)), fuzz.ratio(_stem(q), _stem(a)))
                if len(q) < 4 and s < 100:
                    continue
                s += bonus
                if s > best_s:
                    best, best_s = e, s
        if best and best_s >= 84:
            return best
        # программа, которой нет в каталоге, но есть в меню «Пуск»
        lnk = self.find_shortcut(q, 84)
        if lnk:
            return {"name": Path(lnk).stem, "path": lnk, "proc": [Path(lnk).stem], "src": "menu"}
        return None

    # ---------- действия ----------
    def launch(self, e: dict) -> bool:
        try:
            if e.get("path"):
                winutil.start(e["path"], e.get("args", ""))
            elif e.get("browser"):
                webbrowser.open("https://www.google.com")
            elif e.get("find"):
                lnk = self.find_shortcut(e["find"], 88)
                if lnk:
                    winutil.start(lnk)
                elif e.get("exec_alt"):
                    winutil.start(e["exec_alt"])
                else:
                    return False
            elif e.get("exec"):
                winutil.start(e["exec"], e.get("args", ""))
            else:
                return False
            return True
        except OSError:
            return False

    def open(self, spoken: str) -> str:
        key = norm(spoken)
        site = SITES.get(key)
        if site:
            webbrowser.open(site)
            return f"Открываю {spoken}."
        if "." in key and " " not in key and key.rsplit(".", 1)[-1].isalpha():
            webbrowser.open("https://" + key)
            return f"Открываю {key}."
        e = self.resolve(spoken)
        if not e:
            return f"Не нашла программу «{spoken}». Добавьте её на вкладке «Программы»."
        return f"Запускаю {e['name']}." if self.launch(e) else f"Не получилось запустить {e['name']}. Проверьте путь на вкладке «Программы»."

    def close(self, spoken: str) -> str:
        e = self.resolve(spoken)
        if not e:
            return f"Не нашла программу «{spoken}»."
        names = [*e.get("proc", []), e["name"]]
        n = sum(winutil.close_by_name(x) for x in dict.fromkeys(names))
        return f"Закрываю {e['name']}." if n else f"{e['name']} сейчас не запущена."

    def focus(self, spoken: str) -> str:
        e = self.resolve(spoken)
        name = e["name"] if e else spoken
        for part in dict.fromkeys([name, *(e.get("proc", []) if e else [])]):
            if winutil.focus_window(part):
                return f"Переключаю на {name}."
        return f"Окно «{name}» не найдено."
