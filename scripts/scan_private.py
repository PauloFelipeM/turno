#!/usr/bin/env python3
"""Refuses a tree or a history that still carries private data, before anything is published.

    python3 scripts/scan_private.py --patterns ~/turno-private.txt [--tree] [--history] [REPO]

The patterns file is the owner's and never goes into a commit (the scan refuses one that git
tracks). One pattern per line: a literal, matched without regard to case, or `re:<regex>`; blank
lines and lines starting with `#` are skipped. On top of it the scan always looks for: `/home/<user>`
paths other than the generic `/home/me`, Gmail addresses, an ntfy topic written in a TOML
file (`topic = "..."`) or in a link (`ntfy.sh/<name>`), and GitHub, OpenAI/Anthropic and PyPI tokens.

`--tree` reads the files git would publish from the working tree (tracked, plus untracked ones not
ignored); `--history` reads every blob, path and commit message reachable from any ref. With
neither, both. Exit code 0 when nothing is found, 1 with findings, 2 on a usage error. A finding
names the place and the pattern's line in the patterns file (or a built-in rule's name), never
the matched text, so the output itself leaks nothing.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Placeholder topics the tests and the example file use; any other `topic = "..."` is a finding.
SAFE_TOPICS = {"", "  ", "...", "replace-with-a-random-name", "segredo", "segredo-xyz", "secret-topic", "abc", "{topic}"}
BUILTIN = [
    ("home-path", re.compile(r"/home/(?!me(?:/|\b))[A-Za-z0-9._-]+")),
    ("gmail-address", re.compile(r"@gmail\.com", re.I)),
    ("ntfy-link", re.compile(r"ntfy\.sh/(?!\{)[A-Za-z0-9_-]{3,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("api-key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}")),
]
TOPIC = re.compile(r"""\btopic\s*=\s*["']([^"'\n]*)["']""")


def git(repo, *args, data=None):
    return subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True, check=True).stdout


def load_patterns(path, repo):
    path = Path(path).expanduser().resolve()
    try:
        rel = path.relative_to(Path(repo).resolve())
    except ValueError:
        rel = None
    if rel is not None:
        tracked = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", str(rel)],
                                 capture_output=True).returncode == 0
        if tracked:
            raise SystemExit(f"error: {rel} is tracked by git; the patterns file must never be committed")
    patterns = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        rx = re.compile(text[3:]) if text.startswith("re:") else re.compile(re.escape(text), re.I)
        patterns.append((f"patterns line {number}", rx))
    if not patterns:
        raise SystemExit("error: the patterns file has no pattern")
    return patterns


def findings(text, patterns):
    """The names of the rules that match `text` (each name once)."""
    found = [name for name, rx in [*patterns, *BUILTIN] if rx.search(text)]
    if any(m.group(1) not in SAFE_TOPICS for m in TOPIC.finditer(text)):
        found.append("ntfy-topic")
    return found


def mask(text, patterns):
    """A path as it may be printed: whatever a rule matches in it becomes `***`."""
    for _, rx in [*patterns, *BUILTIN]:
        text = rx.sub("***", text)
    return text


def scan_tree(repo, patterns):
    out = []
    names = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    for raw in filter(None, names):
        rel = raw.decode(errors="replace")
        shown = mask(rel, patterns)
        for rule in findings(rel, patterns):
            out.append(f"tree path {shown}: {rule}")
        path = Path(repo) / rel
        if path.is_file() and not path.is_symlink():
            for rule in findings(path.read_bytes().decode(errors="replace"), patterns):
                out.append(f"tree file {shown}: {rule}")
    return out


def scan_history(repo, patterns):
    out = []
    log = git(repo, "log", "--all", "--format=%H%x00%B%x00%x01").decode(errors="replace")
    for entry in filter(None, (e.strip("\n") for e in log.split("\x01"))):
        sha, _, message = entry.partition("\x00")
        for rule in findings(message, patterns):
            out.append(f"commit message {sha[:12]}: {rule}")
    every_path = git(repo, "-c", "core.quotePath=false", "log", "--all", "--format=", "--name-only", "-z").split(b"\0")
    for path in sorted({p.decode(errors="replace").strip("\n") for p in every_path} - {""}):
        for rule in findings(path, patterns):
            out.append(f"history path {mask(path, patterns)}: {rule}")
    objects = git(repo, "rev-list", "--all", "--objects").decode(errors="replace").splitlines()
    paths = {}
    for line in objects:
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, path)
    if not paths:
        return sorted(set(out))
    batch = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                data="\n".join(paths).encode() + b"\n").decode().splitlines()
    blobs = [line.split()[0] for line in batch if line.endswith(" blob")]
    if blobs:
        raw = git(repo, "cat-file", "--batch", data="\n".join(blobs).encode() + b"\n")
        pos = 0
        while pos < len(raw):
            header_end = raw.index(b"\n", pos)
            sha, _, size = raw[pos:header_end].decode().split(" ")
            start = header_end + 1
            body = raw[start:start + int(size)]
            pos = start + int(size) + 1
            for rule in findings(body.decode(errors="replace"), patterns):
                out.append(f"blob {sha[:12]} ({mask(paths.get(sha, '?'), patterns)}): {rule}")
    return sorted(set(out))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--patterns", required=True, help="the owner's patterns file (never committed)")
    parser.add_argument("--tree", action="store_true")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-dir"], capture_output=True).returncode != 0:
        print(f"error: {repo} is not a git repository", file=sys.stderr)
        return 2
    try:
        patterns = load_patterns(args.patterns, repo)
    except (OSError, re.error) as e:
        print(f"error: cannot read the patterns: {e}", file=sys.stderr)
        return 2
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    both = not args.tree and not args.history
    found = []
    if args.tree or both:
        found += scan_tree(repo, patterns)
    if args.history or both:
        found += scan_history(repo, patterns)
    for line in found:
        print(line)
    print(f"{len(found)} finding(s)" if found else "clean: nothing found")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
