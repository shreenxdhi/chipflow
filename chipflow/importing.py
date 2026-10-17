"""Bounded, read-only RTL discovery. Users confirm ordering and top modules."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

SKIP = {".git", ".venv", "node_modules", ".chipflow", "__pycache__", "build", "dist"}
EXTENSIONS = {
    ".v": "Verilog",
    ".sv": "SystemVerilog",
    ".vh": "Verilog header",
    ".svh": "SystemVerilog header",
}
MAX_FILES = 2000
MAX_BYTES = 16 * 1024 * 1024


def discover_sources(directory: Path | str) -> dict:
    root = Path(directory).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Choose a local project directory")
    files, modules, warnings, includes = [], [], [], set()
    total = 0
    for parent, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(
            d for d in directories if d not in SKIP and not (Path(parent) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(parent) / name
            if path.suffix.lower() not in EXTENSIONS:
                continue
            if path.is_symlink():
                warnings.append(
                    f"Symlink not imported: {path.relative_to(root)}. Declare its target explicitly if trusted."
                )
                continue
            if not path.is_file():
                raise ValueError(f"Only regular source files can be imported: {path}")
            if len(files) >= MAX_FILES or total + path.stat().st_size > MAX_BYTES:
                raise ValueError(
                    "Source discovery exceeds 2000 files / 16 MiB. Choose a narrower directory."
                )
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError(f"Only regular source files can be imported: {path}")
                content = stream.read(MAX_BYTES - total + 1)
            total += len(content)
            if total > MAX_BYTES:
                raise ValueError("Source discovery exceeds the 16 MiB read limit")
            text = content.decode(errors="replace")
            # This lexical inventory does not replace a real HDL compiler/elaborator.
            clean = re.sub(
                r"/\*.*?(?:\*/|\Z)|//[^\n]*", lambda m: "\n" * m[0].count("\n"), text, flags=re.S
            )
            files.append(
                {
                    "path": str(path),
                    "language": EXTENSIONS[path.suffix.lower()],
                    "bytes": path.stat().st_size,
                }
            )
            for match in re.finditer(
                r"\bmodule\s+(?:automatic\s+|static\s+)?([A-Za-z_][A-Za-z0-9_$]*)", clean
            ):
                modules.append(
                    {
                        "name": match[1],
                        "path": str(path),
                        "line": clean.count("\n", 0, match.start()) + 1,
                    }
                )
            for match in re.finditer(r'`include\s+"([^"\n]+)"', clean):
                candidate = (path.parent / match[1]).resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    includes.add(str(candidate.parent))
                else:
                    warnings.append(
                        f"{path.relative_to(root)}: unresolved or external include {match[1]!r}; declare its include directory."
                    )
    definitions = {}
    for module in modules:
        definitions.setdefault(module["name"], []).append(module["path"])
    warnings.extend(
        f"Duplicate module definition {name}: {', '.join(paths)}"
        for name, paths in definitions.items()
        if len(paths) > 1
    )
    warnings.append(
        "Discovery is a lexical inventory, not elaboration. Confirm source order, top module, testbench and external dependencies; run lint/simulation before implementation."
    )
    return {
        "directory": str(root),
        "files": files,
        "modules": modules,
        "includes": sorted(includes),
        "warnings": warnings,
        "authoritative": False,
    }
