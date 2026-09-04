"""Simple JSON file persistence — swappable for a real DB (Supabase/SQLite) later."""
import json
import os


class JsonStore:
    def __init__(self, path):
        self.path = path
        self._data = {}
        self._load()

    def _load(self):
        self._data = {}
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except json.JSONDecodeError:
                self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __setitem__(self, key, val):
        self._data[key] = val

    def __getitem__(self, key):
        return self._data[key]

    def sync(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
