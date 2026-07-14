#!/usr/bin/env python3
"""Compare Vibenix maintenance artifacts against real nixpkgs updates.

This script maps maintenance job directories (named like
``vibenix-maintenance-<ID>-<pname>``) to rows in a maintenance dataset CSV,
then compares the generated package.nix against the real package state in
nixpkgs.

Supported comparison modes:
- full: compare full generated package content against real updated content
- diff: compare base->generated and base->real patches

In interactive mode, you can cycle between jobs and switch mode on the fly.
"""

from __future__ import annotations

import argparse
import curses
import csv
import difflib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


JOB_DIR_PATTERN = re.compile(r"^vibenix-maintenance-(\d+)-.+$")


@dataclass(frozen=True)
class DatasetRow:
    random_order: int
    package_path: str
    pname: str
    pre_version: str
    post_version: str
    parent_commit: str
    commit_hash: str


@dataclass(frozen=True)
class JobEntry:
    job_id: int
    job_dir: Path
    generated_package_path: Path
    dataset_row: DatasetRow


def parse_id_spec(spec: str | None) -> set[int] | None:
    if spec is None:
        return None

    spec = spec.strip()
    if not spec:
        return None

    ids: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError("Invalid --ids value: empty segment")

        if "-" in part:
            bounds = part.split("-", maxsplit=1)
            if len(bounds) != 2:
                raise ValueError(f"Invalid range segment: {part}")
            start = int(bounds[0])
            end = int(bounds[1])
            if start > end:
                raise ValueError(f"Invalid range segment: {part}")
            ids.update(range(start, end + 1))
        else:
            ids.add(int(part))

    return ids


def load_dataset(dataset_path: Path) -> dict[int, DatasetRow]:
    if not dataset_path.is_file():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    rows: dict[int, DatasetRow] = {}
    with dataset_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            random_order = int(row["random_order"])
            rows[random_order] = DatasetRow(
                random_order=random_order,
                package_path=row["package_path"],
                pname=row["pre_pname"],
                pre_version=row["pre_version"],
                post_version=row["post_version"],
                parent_commit=row["parent_commit"],
                commit_hash=row["commit_hash"],
            )
    return rows


def discover_job_dirs(jobs_root: Path, recursive: bool) -> dict[int, Path]:
    if not jobs_root.is_dir():
        raise FileNotFoundError(f"Jobs root not found: {jobs_root}")

    candidates: Iterable[Path]
    if recursive:
        candidates = (p for p in jobs_root.rglob("vibenix-maintenance-*") if p.is_dir())
    else:
        candidates = (p for p in jobs_root.glob("vibenix-maintenance-*") if p.is_dir())

    job_dirs: dict[int, Path] = {}
    for path in sorted(candidates):
        match = JOB_DIR_PATTERN.match(path.name)
        if not match:
            continue
        job_id = int(match.group(1))
        job_dirs[job_id] = path

    return job_dirs


def find_generated_package(job_dir: Path) -> Path:
    package_files = sorted(job_dir.rglob("package.nix"))
    if not package_files:
        raise FileNotFoundError(f"No generated package.nix found under {job_dir}")

    package_files.sort(key=lambda p: (len(p.parts), str(p)))
    return package_files[0]


def git_show_file(nixpkgs_path: Path, commit: str, package_path: str) -> str:
    cmd = ["git", "-C", str(nixpkgs_path), "show", f"{commit}:{package_path}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(
            f"git show failed for {commit}:{package_path}\n"
            f"Command: {' '.join(cmd)}\n"
            f"Error: {stderr}"
        )
    return proc.stdout


def make_unified_diff(
    before_text: str,
    after_text: str,
    from_label: str,
    to_label: str,
    context_lines: int,
) -> str:
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=from_label,
        tofile=to_label,
        n=context_lines,
    )
    return "".join(diff_lines)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def format_header(entry: JobEntry, mode: str, position: tuple[int, int]) -> str:
    row = entry.dataset_row
    index, total = position
    lines = [
        f"=== Job {index}/{total} | ID {entry.job_id} | mode={mode} ===",
        f"job_dir: {entry.job_dir}",
        f"generated: {entry.generated_package_path}",
        f"package_path: {row.package_path}",
        f"version: {row.pre_version} -> {row.post_version}",
        f"base_commit(parent): {row.parent_commit}",
        f"real_update_commit: {row.commit_hash}",
        "",
    ]
    return "\n".join(lines)


def build_full_report(entry: JobEntry, nixpkgs_path: Path, context_lines: int) -> str:
    row = entry.dataset_row
    generated_text = read_text(entry.generated_package_path)
    real_updated_text = git_show_file(nixpkgs_path, row.commit_hash, row.package_path)

    diff_text = make_unified_diff(
        real_updated_text,
        generated_text,
        from_label=f"real@{row.commit_hash}:{row.package_path}",
        to_label=f"generated:{entry.generated_package_path}",
        context_lines=context_lines,
    )
    if not diff_text:
        return "No differences: generated package matches real updated file exactly.\n"
    return diff_text


def build_diff_report(
    entry: JobEntry,
    nixpkgs_path: Path,
    context_lines: int,
    compare_diff_patches: bool,
) -> str:
    row = entry.dataset_row

    base_text = git_show_file(nixpkgs_path, row.parent_commit, row.package_path)
    real_updated_text = git_show_file(nixpkgs_path, row.commit_hash, row.package_path)
    generated_text = read_text(entry.generated_package_path)

    real_patch = make_unified_diff(
        base_text,
        real_updated_text,
        from_label=f"base@{row.parent_commit}:{row.package_path}",
        to_label=f"real@{row.commit_hash}:{row.package_path}",
        context_lines=context_lines,
    )
    generated_patch = make_unified_diff(
        base_text,
        generated_text,
        from_label=f"base@{row.parent_commit}:{row.package_path}",
        to_label=f"generated:{entry.generated_package_path}",
        context_lines=context_lines,
    )

    sections = [
        "--- REAL PATCH (base -> real) ---",
        real_patch or "(no changes)",
        "",
        "--- GENERATED PATCH (base -> generated) ---",
        generated_patch or "(no changes)",
    ]

    if compare_diff_patches:
        patch_to_patch_diff = make_unified_diff(
            real_patch,
            generated_patch,
            from_label="real_patch",
            to_label="generated_patch",
            context_lines=context_lines,
        )
        sections.extend(
            [
                "",
                "--- PATCH-TO-PATCH DIFF (real patch vs generated patch) ---",
                patch_to_patch_diff or "(patches are identical)",
            ]
        )

    return "\n".join(sections) + "\n"


def paginate_or_print(text: str, use_pager: bool) -> None:
    if not use_pager:
        print(text)
        return

    pager = os.environ.get("PAGER", "less -R")
    proc = subprocess.run(
        pager,
        input=text,
        text=True,
        shell=True,
    )
    if proc.returncode != 0:
        print(text)


def split_lines(text: str) -> list[str]:
    return text.splitlines() or [""]


def get_tui_panels(
    entry: JobEntry,
    nixpkgs_path: Path,
    layout: str,
    context_lines: int,
) -> tuple[str, list[str], str, list[str]]:
    row = entry.dataset_row
    base_text = git_show_file(nixpkgs_path, row.parent_commit, row.package_path)
    generated_text = read_text(entry.generated_package_path)
    real_updated_text = git_show_file(nixpkgs_path, row.commit_hash, row.package_path)

    if layout == "generated-real":
        left_title = f"generated: {entry.generated_package_path.name}"
        right_title = f"real@{row.commit_hash[:12]}: {row.package_path}"
        return left_title, split_lines(generated_text), right_title, split_lines(real_updated_text)

    if layout == "base-real":
        left_title = f"base@{row.parent_commit[:12]}: {row.package_path}"
        right_title = f"real@{row.commit_hash[:12]}: {row.package_path}"
        return left_title, split_lines(base_text), right_title, split_lines(real_updated_text)

    if layout == "base-generated":
        left_title = f"base@{row.parent_commit[:12]}: {row.package_path}"
        right_title = f"generated: {entry.generated_package_path.name}"
        return left_title, split_lines(base_text), right_title, split_lines(generated_text)

    if layout != "patches":
        raise ValueError(f"Unknown TUI layout: {layout}")

    generated_patch = make_unified_diff(
        base_text,
        generated_text,
        from_label=f"base@{row.parent_commit}:{row.package_path}",
        to_label=f"generated:{entry.generated_package_path}",
        context_lines=context_lines,
    )
    real_patch = make_unified_diff(
        base_text,
        real_updated_text,
        from_label=f"base@{row.parent_commit}:{row.package_path}",
        to_label=f"real@{row.commit_hash}:{row.package_path}",
        context_lines=context_lines,
    )
    left_title = "patch: base -> generated"
    right_title = "patch: base -> real"
    return left_title, split_lines(generated_patch), right_title, split_lines(real_patch)


def render_colored_line(window: curses.window, y: int, x: int, text: str, width: int) -> None:
    if width <= 0:
        return
    truncated = text[:width]
    color_pair = curses.color_pair(0)
    if truncated.startswith("+") and not truncated.startswith("+++"):
        color_pair = curses.color_pair(2)
    elif truncated.startswith("-") and not truncated.startswith("---"):
        color_pair = curses.color_pair(1)
    elif truncated.startswith("@@"):
        color_pair = curses.color_pair(3)
    elif truncated.startswith("+++") or truncated.startswith("---"):
        color_pair = curses.color_pair(4)
    window.addnstr(y, x, truncated, width, color_pair)


def run_curses_ui(
    entries: list[JobEntry],
    nixpkgs_path: Path,
    mode: str,
    context_lines: int,
) -> None:
    default_layout = "generated-real" if mode == "full" else "patches"
    cache: dict[tuple[int, str, int], tuple[str, list[str], str, list[str]]] = {}

    def _main(stdscr: curses.window) -> None:
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_CYAN, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)

        idx = 0
        y_scroll = 0
        x_scroll = 0
        current_layout = default_layout
        total = len(entries)

        while True:
            entry = entries[idx]
            row = entry.dataset_row
            key_cache = (entry.job_id, current_layout, context_lines)
            if key_cache not in cache:
                cache[key_cache] = get_tui_panels(
                    entry=entry,
                    nixpkgs_path=nixpkgs_path,
                    layout=current_layout,
                    context_lines=context_lines,
                )

            left_title, left_lines, right_title, right_lines = cache[key_cache]

            stdscr.erase()
            height, width = stdscr.getmaxyx()
            split = width // 2
            left_width = max(1, split - 1)
            right_width = max(1, width - split - 1)
            body_top = 2
            body_bottom = height - 2
            body_height = max(1, body_bottom - body_top)

            header = (
                f"ID {entry.job_id} ({idx + 1}/{total}) | {row.pname} | "
                f"{row.pre_version} -> {row.post_version} | layout={current_layout}"
            )
            stdscr.addnstr(0, 0, header, max(1, width - 1), curses.color_pair(3))
            stdscr.addnstr(1, 0, left_title, left_width, curses.color_pair(4))
            stdscr.addnstr(1, split + 1, right_title, right_width, curses.color_pair(4))

            for y in range(body_top, body_bottom):
                stdscr.addch(y, split, ord("|"), curses.color_pair(3))

            max_len = max(len(left_lines), len(right_lines))
            max_scroll = max(0, max_len - body_height)
            if y_scroll > max_scroll:
                y_scroll = max_scroll

            max_left_width = max((len(line) for line in left_lines), default=0)
            max_right_width = max((len(line) for line in right_lines), default=0)
            max_x_scroll = max(0, max(max_left_width - left_width, max_right_width - right_width))
            if x_scroll > max_x_scroll:
                x_scroll = max_x_scroll

            for row_y in range(body_height):
                source_idx = y_scroll + row_y
                target_y = body_top + row_y
                if source_idx < len(left_lines):
                    render_colored_line(
                        stdscr,
                        target_y,
                        0,
                        left_lines[source_idx][x_scroll:],
                        left_width,
                    )
                if source_idx < len(right_lines):
                    render_colored_line(
                        stdscr,
                        target_y,
                        split + 1,
                        right_lines[source_idx][x_scroll:],
                        right_width,
                    )

            help_text = (
                "q quit | n/p job | 1 gen-real | 2 base-real | 3 base-gen | 4 patches | "
                "j/k ↑/↓ vertical | h/l ←/→ horizontal"
            )
            stdscr.addnstr(height - 1, 0, help_text, max(1, width - 1), curses.color_pair(3))
            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord("q"), ord("Q")):
                return
            if key in (ord("n"), ord("N")):
                idx = (idx + 1) % total
                y_scroll = 0
                x_scroll = 0
                continue
            if key in (ord("p"), ord("P")):
                idx = (idx - 1) % total
                y_scroll = 0
                x_scroll = 0
                continue
            if key in (ord("m"), ord("M")):
                current_layout = "patches" if current_layout != "patches" else "generated-real"
                y_scroll = 0
                x_scroll = 0
                continue
            if key in (ord("f"), ord("F")):
                current_layout = "generated-real"
                y_scroll = 0
                x_scroll = 0
                continue
            if key in (ord("d"), ord("D")):
                current_layout = "patches"
                y_scroll = 0
                x_scroll = 0
                continue
            if key == ord("1"):
                current_layout = "generated-real"
                y_scroll = 0
                x_scroll = 0
                continue
            if key == ord("2"):
                current_layout = "base-real"
                y_scroll = 0
                x_scroll = 0
                continue
            if key == ord("3"):
                current_layout = "base-generated"
                y_scroll = 0
                x_scroll = 0
                continue
            if key == ord("4"):
                current_layout = "patches"
                y_scroll = 0
                x_scroll = 0
                continue
            if key in (ord("j"), curses.KEY_DOWN):
                y_scroll += 1
                continue
            if key in (ord("k"), curses.KEY_UP):
                y_scroll = max(0, y_scroll - 1)
                continue
            if key in (ord("l"), curses.KEY_RIGHT):
                x_scroll += 4
                continue
            if key in (ord("h"), curses.KEY_LEFT):
                x_scroll = max(0, x_scroll - 4)
                continue
            if key == curses.KEY_NPAGE:
                y_scroll += max(1, body_height - 1)
                continue
            if key == curses.KEY_PPAGE:
                y_scroll = max(0, y_scroll - max(1, body_height - 1))
                continue

    curses.wrapper(_main)


def render_job(
    entry: JobEntry,
    nixpkgs_path: Path,
    mode: str,
    context_lines: int,
    compare_diff_patches: bool,
    position: tuple[int, int],
) -> str:
    if mode not in {"full", "diff"}:
        raise ValueError(f"Unknown mode: {mode}")

    header = format_header(entry, mode, position)
    if mode == "full":
        body = build_full_report(entry, nixpkgs_path, context_lines)
    else:
        body = build_diff_report(entry, nixpkgs_path, context_lines, compare_diff_patches)
    return header + body


def run_interactive(
    entries: list[JobEntry],
    nixpkgs_path: Path,
    mode: str,
    context_lines: int,
    compare_diff_patches: bool,
    use_pager: bool,
) -> None:
    index = 0
    total = len(entries)

    help_text = (
        "Commands: [n]ext, [p]rev, [m]ode toggle, [f]ull, [d]iff, "
        "[g <id>] goto id, [q]uit"
    )

    while True:
        entry = entries[index]
        report = render_job(
            entry,
            nixpkgs_path=nixpkgs_path,
            mode=mode,
            context_lines=context_lines,
            compare_diff_patches=compare_diff_patches,
            position=(index + 1, total),
        )
        paginate_or_print(report, use_pager=use_pager)
        print(help_text)
        command = input("compare> ").strip()

        if command in {"q", "quit", "exit"}:
            return
        if command in {"n", "next", ""}:
            index = (index + 1) % total
            continue
        if command in {"p", "prev", "previous"}:
            index = (index - 1) % total
            continue
        if command in {"m", "mode"}:
            mode = "diff" if mode == "full" else "full"
            continue
        if command in {"f", "full"}:
            mode = "full"
            continue
        if command in {"d", "diff"}:
            mode = "diff"
            continue
        if command.startswith("g "):
            target = command[2:].strip()
            if target.isdigit():
                target_id = int(target)
                for idx, candidate in enumerate(entries):
                    if candidate.job_id == target_id:
                        index = idx
                        break
                else:
                    print(f"No loaded job with ID {target_id}")
            else:
                print("Usage: g <id>")
            continue

        print(f"Unknown command: {command}")


def build_entries(
    dataset_rows: dict[int, DatasetRow],
    job_dirs: dict[int, Path],
    id_filter: set[int] | None,
) -> list[JobEntry]:
    selected_ids = sorted(job_dirs.keys())
    if id_filter is not None:
        selected_ids = [job_id for job_id in selected_ids if job_id in id_filter]

    entries: list[JobEntry] = []
    for job_id in selected_ids:
        if job_id not in dataset_rows:
            continue

        job_dir = job_dirs[job_id]
        try:
            generated_package_path = find_generated_package(job_dir)
        except FileNotFoundError:
            continue
        entries.append(
            JobEntry(
                job_id=job_id,
                job_dir=job_dir,
                generated_package_path=generated_package_path,
                dataset_row=dataset_rows[job_id],
            )
        )

    return entries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Vibenix maintenance results to real nixpkgs package updates.",
    )
    parser.add_argument(
        "--nixpkgs-path",
        dest="nixpkgs_path_opt",
        metavar="NIXPKGS_PATH",
        help="Path to nixpkgs git repository (required).",
    )
    parser.add_argument(
        "nixpkgs_path",
        nargs="?",
        metavar="NIXPKGS_PATH",
        help="Positional nixpkgs path alternative to --nixpkgs-path.",
    )
    parser.add_argument(
        "--dataset",
        default="research/maintenance_feb.csv",
        help="Path to maintenance CSV dataset (default: research/maintenance_feb.csv).",
    )
    parser.add_argument(
        "--jobs-root",
        default=".",
        help="Directory containing vibenix-maintenance-* job directories.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search jobs root recursively for vibenix-maintenance-* directories.",
    )
    parser.add_argument(
        "--ids",
        help='Optional ID filter, e.g. "1-10,15,20".',
    )
    parser.add_argument(
        "--mode",
        choices=["full", "diff"],
        default="full",
        help="Comparison mode (default: full).",
    )
    parser.add_argument(
        "--context-lines",
        type=int,
        default=3,
        help="Unified diff context line count (default: 3).",
    )
    parser.add_argument(
        "--compare-diff-patches",
        action="store_true",
        help="In diff mode, also diff the two patch outputs against each other.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Force interactive navigation mode.",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Disable interactive mode and print each selected report once.",
    )
    parser.add_argument(
        "--no-pager",
        action="store_true",
        help="Print directly instead of using PAGER/less.",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Force curses split-pane UI in interactive mode.",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Disable curses UI and use line-oriented interactive mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    nixpkgs_path_arg = args.nixpkgs_path_opt or args.nixpkgs_path
    if not nixpkgs_path_arg:
        print("error: provide NIXPKGS_PATH or --nixpkgs-path", file=sys.stderr)
        return 2

    nixpkgs_path = Path(nixpkgs_path_arg).expanduser().resolve()
    dataset_path = Path(args.dataset).expanduser().resolve()
    jobs_root = Path(args.jobs_root).expanduser().resolve()

    if not nixpkgs_path.is_dir():
        print(f"error: --nixpkgs-path is not a directory: {nixpkgs_path}", file=sys.stderr)
        return 2
    if not (nixpkgs_path / ".git").exists():
        print(f"error: --nixpkgs-path is not a git repository: {nixpkgs_path}", file=sys.stderr)
        return 2

    try:
        id_filter = parse_id_spec(args.ids)
        dataset_rows = load_dataset(dataset_path)
        job_dirs = discover_job_dirs(jobs_root, recursive=args.recursive)
        entries = build_entries(dataset_rows, job_dirs, id_filter)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not entries:
        print(
            "error: no matching jobs found. "
            "Check --jobs-root/--recursive and --ids filters.",
            file=sys.stderr,
        )
        return 1

    if args.no_interactive:
        interactive = False
    elif args.interactive:
        interactive = True
    else:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()

    use_pager = not args.no_pager

    if interactive:
        use_tui = args.tui or (not args.no_tui and sys.stdin.isatty() and sys.stdout.isatty())
        if use_tui:
            run_curses_ui(
                entries=entries,
                nixpkgs_path=nixpkgs_path,
                mode=args.mode,
                context_lines=args.context_lines,
            )
        else:
            run_interactive(
                entries=entries,
                nixpkgs_path=nixpkgs_path,
                mode=args.mode,
                context_lines=args.context_lines,
                compare_diff_patches=args.compare_diff_patches,
                use_pager=use_pager,
            )
        return 0

    for idx, entry in enumerate(entries, start=1):
        report = render_job(
            entry,
            nixpkgs_path=nixpkgs_path,
            mode=args.mode,
            context_lines=args.context_lines,
            compare_diff_patches=args.compare_diff_patches,
            position=(idx, len(entries)),
        )
        try:
            print(report)
        except BrokenPipeError:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
