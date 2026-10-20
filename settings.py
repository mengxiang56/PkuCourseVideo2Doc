"""Non-secret preferences. Account passwords are never written here."""
import json
from pathlib import Path


def load_settings(root: Path) -> dict:
    try:
        data = json.loads((root / "data" / "settings.json").read_text(encoding="utf-8"))
        return {"auto_login": data.get("auto_login") is not False,
                "background_browser": data.get("background_browser") is not False}
    except (OSError, ValueError, AttributeError):
        return {"auto_login": True, "background_browser": True}


def save_settings(root: Path, *, auto_login: bool | None = None, background_browser: bool | None = None):
    path = root / "data" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write only this schema, never caller-provided dictionaries with credentials.
    temporary = path.with_suffix(".tmp")
    settings = load_settings(root)
    if auto_login is not None:
        settings["auto_login"] = bool(auto_login)
    if background_browser is not None:
        settings["background_browser"] = bool(background_browser)
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    temporary.replace(path)
