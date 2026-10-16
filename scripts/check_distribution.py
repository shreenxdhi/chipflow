"""Reject local artifacts and private paths in release distributions."""

import argparse
import os
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

PRIVATE_HOME = os.fsencode(str(Path.home()))


def check(name, data):
    parts = Path(name).parts
    assert not any(part in {".venv", ".chipflow", "__pycache__", ".git"} for part in parts), name
    assert not name.endswith(("HANDOFF.md", ".pyc", ".log")), name
    if name.endswith(
        (".py", ".md", ".json", ".yaml", ".html", ".js", ".css", ".toml", ".txt", ".rb")
    ):
        assert PRIVATE_HOME not in data, f"Private/development content in {name}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    for path in sorted(args.directory.iterdir()):
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    check(name, archive.read(name))
                assert "chipflow/examples/counter/chipflow.yaml" in archive.namelist()
                assert "chipflow/static/index.html" in archive.namelist()
                metadata_name = next(
                    name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
                )
                metadata = BytesParser().parsebytes(archive.read(metadata_name))
                runtime = sorted(
                    value.replace(" ", "")
                    for value in metadata.get_all("Requires-Dist", [])
                    if ";" not in value
                )
                expected = sorted(
                    line.strip()
                    for line in (Path(__file__).resolve().parents[1] / "requirements-runtime.txt")
                    .read_text()
                    .splitlines()
                    if line.strip() and not line.startswith("#")
                )
                assert runtime == expected, (
                    "Wheel runtime requirements differ from the reviewed dependency pins"
                )
        elif path.name.endswith(".tar.gz"):
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    if member.isfile():
                        check(member.name, archive.extractfile(member).read())
        else:
            continue
        print(f"{path.name}: content check passed")


if __name__ == "__main__":
    main()
