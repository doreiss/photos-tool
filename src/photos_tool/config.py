"""Configuration loading for photos-tool.

The config file is intentionally small TOML with no secrets. SMB credentials live
in the macOS Keychain; the config only stores the share URL and expected mount
path.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:  # pragma: no cover - exercised on Python 3.10 via tomli.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_DIRECTORY_TEMPLATE = "{created.year}/{created.mm}"
DEFAULT_FILENAME_TEMPLATE = "{original_name}"
DEFAULT_STATE_DIR = "~/.local/state/photos-tool"


class ConfigError(ValueError):
    """Raised when the TOML config is malformed."""


@dataclass(frozen=True)
class DestinationConfig:
    smb_url: str = ""
    mount_point: str = ""
    subpath: str = ""


@dataclass(frozen=True)
class ExportConfig:
    directory_template: str = DEFAULT_DIRECTORY_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    download_missing: bool = True
    use_photokit: bool = False
    retry: int = 3


@dataclass(frozen=True)
class CopiesConfig:
    jpeg: bool = False
    jpeg_quality: float = 0.9
    mp4: bool = False
    mp4_crf: int = 20


@dataclass(frozen=True)
class StateConfig:
    exportdb_dir: str = f"{DEFAULT_STATE_DIR}/exportdb"
    log_dir: str = f"{DEFAULT_STATE_DIR}/logs"


@dataclass(frozen=True)
class RemoveConfig:
    max_delete: int = 500


@dataclass(frozen=True)
class Config:
    destination: DestinationConfig = DestinationConfig()
    export: ExportConfig = ExportConfig()
    copies: CopiesConfig = CopiesConfig()
    state: StateConfig = StateConfig()
    remove: RemoveConfig = RemoveConfig()

    def destination_path(self, override: str | None = None) -> Path:
        """Return the final export destination, including any configured subpath."""
        base = override or self.destination.mount_point
        if not base:
            raise ConfigError(
                "destination is required: set destination.mount_point in config "
                "(or pass a destination to plan/doctor)"
            )
        path = Path(base).expanduser()
        if self.destination.subpath:
            path = path / self.destination.subpath
        return path


def default_config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root).expanduser() / "photos-tool" / "config.toml"
    return Path("~/.config/photos-tool/config.toml").expanduser()


def resolved_exportdb_path(destination: str | Path, exportdb_dir: str | Path | None = None) -> Path:
    """Return a stable local export DB path for one destination tree."""
    root = Path(exportdb_dir or StateConfig().exportdb_dir).expanduser()
    canonical = str(Path(destination).expanduser().resolve(strict=False))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return root / f"{digest}.osxphotos_export.db"


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path).expanduser() if path is not None else default_config_path()
    if not config_path.exists():
        return Config()

    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"could not read {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config root must be a TOML table")

    return Config(
        destination=_parse_destination(_table(raw, "destination")),
        export=_parse_export(_table(raw, "export")),
        copies=_parse_copies(_table(raw, "copies")),
        state=_parse_state(_table(raw, "state")),
        remove=_parse_remove(_table(raw, "remove")),
    )


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _parse_destination(raw: dict[str, Any]) -> DestinationConfig:
    smb_url = _str(raw, "smb_url", "")
    if smb_url:
        validate_smb_url(smb_url)
    return DestinationConfig(
        smb_url=smb_url,
        mount_point=_str(raw, "mount_point", ""),
        subpath=_str(raw, "subpath", ""),
    )


def _parse_export(raw: dict[str, Any]) -> ExportConfig:
    retry = _int(raw, "retry", 3)
    if retry < 0:
        raise ConfigError("export.retry must be non-negative")
    return ExportConfig(
        directory_template=_str(raw, "directory_template", DEFAULT_DIRECTORY_TEMPLATE),
        filename_template=_str(raw, "filename_template", DEFAULT_FILENAME_TEMPLATE),
        download_missing=_bool(raw, "download_missing", True),
        use_photokit=_bool(raw, "use_photokit", False),
        retry=retry,
    )


def _parse_copies(raw: dict[str, Any]) -> CopiesConfig:
    jpeg_quality = _float(raw, "jpeg_quality", 0.9)
    if not 0 <= jpeg_quality <= 1:
        raise ConfigError("copies.jpeg_quality must be between 0.0 and 1.0")
    mp4_crf = _int(raw, "mp4_crf", 20)
    if not 0 <= mp4_crf <= 51:
        raise ConfigError("copies.mp4_crf must be between 0 and 51")
    return CopiesConfig(
        jpeg=_bool(raw, "jpeg", False),
        jpeg_quality=jpeg_quality,
        mp4=_bool(raw, "mp4", False),
        mp4_crf=mp4_crf,
    )


def _parse_state(raw: dict[str, Any]) -> StateConfig:
    return StateConfig(
        exportdb_dir=_str(raw, "exportdb_dir", StateConfig().exportdb_dir),
        log_dir=_str(raw, "log_dir", StateConfig().log_dir),
    )


def _parse_remove(raw: dict[str, Any]) -> RemoveConfig:
    max_delete = _int(raw, "max_delete", 500)
    if max_delete < 1:
        raise ConfigError("remove.max_delete must be at least 1")
    return RemoveConfig(max_delete=max_delete)


def _str(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


def _int(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    return value


def _float(raw: dict[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} must be a number")
    return float(value)


def validate_smb_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "smb" or not parsed.hostname or not parsed.path.strip("/"):
        raise ConfigError("destination.smb_url must look like smb://server/Share")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ConfigError("destination.smb_url must not contain credentials")
    # Reject path-traversal / control-char share segments (e.g. smb://host/../evil): the share
    # name flows into the derived mount point, so a "." or ".." segment could point /Volumes/..
    # at the wrong directory. (The first segment is the SMB share itself, parsed correctly.)
    for segment in parsed.path.split("/"):
        if segment in {".", ".."} or any(ch < " " for ch in segment):
            raise ConfigError("destination.smb_url has an invalid share path segment")
