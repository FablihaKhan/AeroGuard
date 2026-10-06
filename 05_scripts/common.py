"""Shared helpers: project paths, config loading, logging and download caching."""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

BRONZE = ROOT / CONFIG["paths"]["bronze"]
SILVER = ROOT / CONFIG["paths"]["silver"]
GOLD = ROOT / CONFIG["paths"]["gold"]
OUTPUTS = ROOT / CONFIG["paths"]["outputs"]
DOCS = ROOT / CONFIG["paths"]["docs"]

CRS = CONFIG["project"]["crs_projected"]
CRS_GEO = CONFIG["project"]["crs_geographic"]

USER_AGENT = "shyamnagar-thesis-research/0.1 (BUET CSE)"


def get_logger(name: str) -> logging.Logger:
    """Logger that writes to the console and to 00_documentation/processing_log.txt."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logfile = logging.FileHandler(DOCS / "processing_log.txt", encoding="utf-8")
    logfile.setFormatter(fmt)
    logger.addHandler(console)
    logger.addHandler(logfile)
    return logger


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, logger: logging.Logger | None = None,
             overwrite: bool = False, timeout: int = 300) -> Path:
    """Stream a URL to dest; skip if the file already exists (bronze data is immutable)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        if logger:
            logger.info("cached: %s", dest.relative_to(ROOT))
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=timeout,
                      headers={"User-Agent": USER_AGENT}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    if logger:
        logger.info("downloaded: %s (%.1f MB)", dest.relative_to(ROOT), dest.stat().st_size / 1e6)
    return dest
