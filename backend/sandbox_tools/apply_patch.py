#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


class PatchError(Exception):
    pass


@dataclass
class AddFile:
    path: str
    lines: list[str]


@dataclass
class DeleteFile:
    path: str


@dataclass
class Hunk:
    header: str | None
    lines: list[tuple[str, str]]
    eof: bool = False


@dataclass
class UpdateFile:
    path: str
    move_to: str | None
    hunks: list[Hunk]


FileOp = AddFile | DeleteFile | UpdateFile


def _is_file_header(line: str) -> bool:
    return (
        line.startswith("*** Add File: ")
        or line.startswith("*** Delete File: ")
        or line.startswith("*** Update File: ")
    )


def _read_patch() -> str:
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "-":
            return sys.stdin.read()
        if arg.startswith("*** Begin Patch"):
            return arg
        path = Path(arg)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="surrogateescape")
        return "\n".join(sys.argv[1:])
    return sys.stdin.read()


def _parse_hunk_header(line: str) -> str | None:
    if line == "@@":
        return None
    if line.startswith("@@ "):
        return line[3:]
    raise PatchError(f"Invalid hunk header: {line}")


def parse_patch(patch: str) -> list[FileOp]:
    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch":
        raise PatchError("Patch must start with *** Begin Patch")

    ops: list[FileOp] = []
    i = 1
    while i < len(lines):
        line = lines[i]
        if line == "*** End Patch":
            if i != len(lines) - 1:
                trailing = [item for item in lines[i + 1 :] if item.strip()]
                if trailing:
                    raise PatchError("Unexpected content after *** End Patch")
            return ops

        if line.startswith("*** Add File: "):
            path = line.removeprefix("*** Add File: ")
            i += 1
            add_lines: list[str] = []
            while i < len(lines) and lines[i] != "*** End Patch" and not _is_file_header(lines[i]):
                if not lines[i].startswith("+"):
                    raise PatchError(f"Add File lines must start with '+': {lines[i]}")
                add_lines.append(lines[i][1:])
                i += 1
            if not add_lines:
                raise PatchError(f"Add File needs at least one content line: {path}")
            ops.append(AddFile(path=path, lines=add_lines))
            continue

        if line.startswith("*** Delete File: "):
            ops.append(DeleteFile(path=line.removeprefix("*** Delete File: ")))
            i += 1
            continue

        if line.startswith("*** Update File: "):
            path = line.removeprefix("*** Update File: ")
            i += 1
            move_to = None
            if i < len(lines) and lines[i].startswith("*** Move to: "):
                move_to = lines[i].removeprefix("*** Move to: ")
                i += 1

            hunks: list[Hunk] = []
            while i < len(lines) and lines[i] != "*** End Patch" and not _is_file_header(lines[i]):
                if not lines[i].startswith("@@"):
                    raise PatchError(f"Update hunks must start with '@@': {lines[i]}")

                header = _parse_hunk_header(lines[i])
                i += 1
                hunk_lines: list[tuple[str, str]] = []
                eof = False
                while i < len(lines):
                    current = lines[i]
                    if current == "*** End of File":
                        eof = True
                        i += 1
                        break
                    if current == "*** End Patch" or _is_file_header(current) or current.startswith("@@"):
                        break
                    if not current or current[0] not in " +-":
                        raise PatchError(f"Invalid hunk line: {current}")
                    hunk_lines.append((current[0], current[1:]))
                    i += 1
                hunks.append(Hunk(header=header, lines=hunk_lines, eof=eof))
            if not hunks and move_to is None:
                raise PatchError(f"Update File needs hunks or Move to: {path}")
            ops.append(UpdateFile(path=path, move_to=move_to, hunks=hunks))
            continue

        raise PatchError(f"Unknown patch operation: {line}")

    raise PatchError("Patch must end with *** End Patch")


def _target_path(root: Path, raw_path: str) -> Path:
    if not raw_path or raw_path.strip() != raw_path:
        raise PatchError(f"Invalid path: {raw_path!r}")
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        raise PatchError(f"Patch paths must be relative and stay inside the workspace: {raw_path}")
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise PatchError(f"Patch path escapes workspace: {raw_path}")
    if target == root:
        raise PatchError("Patch target must be a file path")
    return target


def _read_text_lines(path: Path) -> tuple[list[str], bool]:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    return text.splitlines(), text.endswith("\n")


def _write_text_lines(path: Path, lines: list[str], final_newline: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if lines and final_newline:
        text += "\n"
    path.write_text(text, encoding="utf-8", errors="surrogateescape")


def _find_line_containing(lines: list[str], needle: str, start: int) -> int | None:
    if not needle:
        return None
    for idx in range(max(start, 0), len(lines)):
        if needle in lines[idx]:
            return idx
    for idx in range(0, max(start, 0)):
        if needle in lines[idx]:
            return idx
    return None


def _find_sequence(lines: list[str], sequence: list[str], start: int) -> int | None:
    if not sequence:
        return max(0, min(start, len(lines)))
    last_start = len(lines) - len(sequence)
    for idx in range(max(start, 0), last_start + 1):
        if lines[idx : idx + len(sequence)] == sequence:
            return idx
    for idx in range(0, max(start, 0)):
        if lines[idx : idx + len(sequence)] == sequence:
            return idx
    return None


def _apply_hunks(path: Path, hunks: list[Hunk]) -> None:
    lines, final_newline = _read_text_lines(path)
    cursor = 0

    for hunk in hunks:
        search_start = cursor
        if hunk.header:
            header_start = _find_line_containing(lines, hunk.header.strip(), cursor)
            if header_start is not None:
                search_start = header_start
                if not hunk.lines:
                    cursor = header_start
                    continue

        old_lines: list[str] = []
        new_lines: list[str] = []
        for prefix, text in hunk.lines:
            if prefix in (" ", "-"):
                old_lines.append(text)
            if prefix in (" ", "+"):
                new_lines.append(text)

        if not old_lines:
            pos = len(lines) if hunk.eof else search_start
        else:
            pos = _find_sequence(lines, old_lines, search_start)
            if pos is None:
                preview = "\n".join(old_lines[:8])
                raise PatchError(f"Could not find patch context in {path}:\n{preview}")

        lines[pos : pos + len(old_lines)] = new_lines
        cursor = pos + len(new_lines)

    _write_text_lines(path, lines, final_newline=final_newline)


def apply_ops(ops: list[FileOp], root: Path) -> None:
    for op in ops:
        if isinstance(op, AddFile):
            target = _target_path(root, op.path)
            if target.exists():
                raise PatchError(f"Add File target already exists: {op.path}")
            _write_text_lines(target, op.lines)
            continue

        if isinstance(op, DeleteFile):
            target = _target_path(root, op.path)
            if not target.exists():
                raise PatchError(f"Delete File target does not exist: {op.path}")
            if target.is_dir():
                raise PatchError(f"Delete File target is a directory: {op.path}")
            target.unlink()
            continue

        if isinstance(op, UpdateFile):
            source = _target_path(root, op.path)
            if not source.exists():
                raise PatchError(f"Update File target does not exist: {op.path}")
            if source.is_dir():
                raise PatchError(f"Update File target is a directory: {op.path}")

            if op.hunks:
                _apply_hunks(source, op.hunks)

            if op.move_to:
                destination = _target_path(root, op.move_to)
                if destination.exists() and destination != source:
                    raise PatchError(f"Move target already exists: {op.move_to}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
            continue

        raise PatchError(f"Unsupported patch op: {op}")


def main() -> int:
    try:
        patch = _read_patch()
        ops = parse_patch(patch)
        apply_ops(ops, Path.cwd().resolve())
    except PatchError as exc:
        print(f"apply_patch: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"apply_patch: {exc}", file=sys.stderr)
        return 1

    print("Done!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
