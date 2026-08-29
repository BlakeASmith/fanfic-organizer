"""Detect KOReader-compatible devices before deploy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DEFAULT_KOREADER_SUBDIR = ".adds/koreader"
ANDROID_KOREADER_DIR = "koreader"
KOREADER_MARKERS = ("settings", "plugins", "cache", "data")
KOBO_DEVICE_NAMES = frozenset({"KOBO", "KOBOTOUCH", "KOBOTOUCHEXTENDED"})


class KoreaderDetectionError(Exception):
    """Raised when the connected device is not a deployable KOReader target."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class KoreaderMount:
    kind: Literal["kobo", "android"]
    storage_prefix: str
    koreader_root: Path


def device_plugboard_name(device: Any) -> str:
    return str(getattr(device, "DEVICE_PLUGBOARD_NAME", "") or "")


def is_kobo_device(device: Any) -> bool:
    if device_plugboard_name(device) in KOBO_DEVICE_NAMES:
        return True
    return "KOBO" in type(device).__name__.upper()


def is_mtp_device(device: Any) -> bool:
    if device_plugboard_name(device) == "MTP_DEVICE":
        return True
    return type(device).__name__ == "MTP_DEVICE"


def storage_prefixes(device: Any) -> list[str]:
    prefixes: list[str] = []
    seen: set[str] = set()
    for attr in ("_main_prefix", "_card_a_prefix", "_card_b_prefix"):
        prefix = getattr(device, attr, None)
        if not prefix:
            continue
        text = str(prefix).rstrip("/\\")
        if not text or text in seen:
            continue
        seen.add(text)
        prefixes.append(text)
    return prefixes


def has_koreader_markers(root: Path) -> bool:
    if not root.is_dir():
        return False
    return any((root / name).is_dir() for name in KOREADER_MARKERS)


def _kobo_storage_root(prefix: Path) -> bool:
    return (prefix / ".kobo").is_dir()


def _koreader_root_for_prefix(
    prefix: Path,
    *,
    device: Any,
    koreader_subdir: str,
) -> KoreaderMount | None:
    android_root = prefix / ANDROID_KOREADER_DIR
    if has_koreader_markers(android_root):
        return KoreaderMount("android", str(prefix), android_root)

    if is_kobo_device(device) and _kobo_storage_root(prefix):
        kobo_root = prefix / Path(koreader_subdir.lstrip("/"))
        if has_koreader_markers(kobo_root):
            return KoreaderMount("kobo", str(prefix), kobo_root)
    return None


def detect_koreader_mounts(
    device: Any,
    *,
    koreader_subdir: str = DEFAULT_KOREADER_SUBDIR,
) -> list[KoreaderMount]:
    """Return KOReader data roots on the connected device, or raise."""
    prefixes = storage_prefixes(device)
    if not prefixes:
        if is_mtp_device(device):
            raise KoreaderDetectionError(
                "This device is connected over MTP (common for Android phones). "
                "Fanfic Organizer cannot write KOReader files over MTP.",
                hint=(
                    "Use a Kobo with KOReader over USB, or connect the device "
                    "storage with Calibre's Connect to folder if your computer "
                    "mounts it as a drive."
                ),
            )
        raise KoreaderDetectionError(
            "Could not locate storage on the connected device.",
            hint="Wait for Calibre to finish connecting, then try again.",
        )

    mounts: list[KoreaderMount] = []
    seen_roots: set[str] = set()
    for prefix in prefixes:
        mount = _koreader_root_for_prefix(
            Path(prefix),
            device=device,
            koreader_subdir=koreader_subdir,
        )
        if mount is None:
            continue
        key = str(mount.koreader_root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        mounts.append(mount)

    if mounts:
        return mounts

    if is_kobo_device(device):
        raise KoreaderDetectionError(
            "This Kobo does not appear to have KOReader set up yet.",
            hint=(
                "Install KOReader on the Kobo and open it once so "
                f"{koreader_subdir}/ exists, then deploy again."
            ),
        )

    raise KoreaderDetectionError(
        "The connected device is not a compatible KOReader device.",
        hint=(
            "Connect a Kobo with KOReader, or Android storage that already "
            f"contains a {ANDROID_KOREADER_DIR}/ folder (with settings, plugins, "
            "or cache inside)."
        ),
    )


def koreader_deployable(device: Any, *, koreader_subdir: str = DEFAULT_KOREADER_SUBDIR) -> bool:
    try:
        detect_koreader_mounts(device, koreader_subdir=koreader_subdir)
    except KoreaderDetectionError:
        return False
    return True
