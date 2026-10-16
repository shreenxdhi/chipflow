"""Generate a checksummed Homebrew tap formula from a release and pinned dependencies."""

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdist", required=True, type=Path)
    parser.add_argument("--requirements", default="requirements-runtime.txt", type=Path)
    parser.add_argument("--repository", default="shreenxdhi/chipflow")
    parser.add_argument("--output", default="packaging/homebrew/Formula/chipflow.rb", type=Path)
    args = parser.parse_args()
    assert re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository)
    match = re.fullmatch(r"chipflow-(\d+\.\d+\.\d+)\.tar\.gz", args.sdist.name)
    if not match:
        raise ValueError("Expected a versioned chipflow source distribution")
    version = match[1]
    resources = []
    for line in args.requirements.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, pinned = line.split("==", 1)
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{name}/{pinned}/json", timeout=30
        ) as response:
            metadata = json.load(response)
        source = next((item for item in metadata["urls"] if item["packagetype"] == "sdist"), None)
        if source is None:
            raise ValueError(f"No source distribution for {name} {pinned}")
        resources.append(
            dict(
                name=name.lower().replace("_", "-"),
                version=pinned,
                url=source["url"],
                sha256=source["digests"]["sha256"],
            )
        )
    sha = hashlib.sha256(args.sdist.read_bytes()).hexdigest()
    lines = [
        "class Chipflow < Formula",
        "  include Language::Python::Virtualenv",
        "",
        '  desc "Local RTL-to-GDS workflow manager with terminal and browser dashboards"',
        f'  homepage "https://github.com/{args.repository}"',
        f'  url "https://github.com/{args.repository}/releases/download/v{version}/{args.sdist.name}"',
        f'  sha256 "{sha}"',
        '  license "Apache-2.0"',
        "",
        "  depends_on :linux",
        '  depends_on "rust" => :build',
        '  depends_on "libyaml"',
        '  depends_on "python@3.14"',
        "",
    ]
    for item in resources:
        lines.extend(
            [
                f'  resource "{item["name"]}" do',
                f'    url "{item["url"]}"',
                f'    sha256 "{item["sha256"]}"',
                "  end",
                "",
            ]
        )
    lines += [
        "  def install",
        "    virtualenv_install_with_resources",
        "  end",
        "",
        "  def caveats",
        "    <<~EOS",
        "      Check EDA prerequisites and installation suggestions:",
        "        chipflow setup",
        "      Run your first self-checking counter (requires Icarus Verilog):",
        "        chipflow quickstart first-chip",
        "      Learn the flow: chipflow guide",
        "      Customize Tcl: chipflow init custom-chip --expert",
        "      EDA tools and licensed platform data are installed separately.",
        "    EOS",
        "  end",
        "",
        "  test do",
        '    assert_match version.to_s, shell_output("#{bin}/chipflow --version")',
        '    system bin/"chipflow", "init", testpath/"demo", "--expert"',
        '    assert_path_exists testpath/"demo/tcl/common.tcl"',
        '    assert_path_exists testpath/"demo/GUIDE.md"',
        '    assert_match "Your first RTL-to-GDS", shell_output("#{bin}/chipflow guide")',
        '    assert_path_exists testpath/"demo/counter.v"',
        '    assert_match "rtl:", (testpath/"demo/chipflow.yaml").read',
        '    system bin/"chipflow", "--runs", testpath/"runs", "backup", testpath/"history.tar.gz"',
        '    system bin/"chipflow", "--runs", testpath/"restored", "restore", testpath/"history.tar.gz"',
        "  end",
        "end",
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines))
    (args.output.parent.parent / "dependencies.json").write_text(
        json.dumps(resources, indent=2) + "\n"
    )
    print(f"Generated {args.output} with {len(resources)} pinned resources")


if __name__ == "__main__":
    main()
