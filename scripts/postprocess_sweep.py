#!/usr/bin/env python3
"""Process miluphcuda HDF5 frames and summarize final aggregate masses and composition."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


TOOLS_DIR = Path(__file__).resolve().parents[1] / "src/fast_identify_fragments_and_calc_aggregates"
FRAME_NAME = re.compile(r"impact\.(\d+)\.h5")


def read_case_table(path):
    """Read case-table rows in order, omitting generation bookkeeping columns."""
    lines = path.read_text().splitlines()
    if len(lines) < 2:
        raise ValueError(f"incomplete case table: {path}")
    headers = [field.strip() for field in lines[0].split("|")]
    if "case_directory" not in headers or len(set(headers)) != len(headers):
        raise ValueError(f"invalid case table header: {path}")
    keep = [index for index, name in enumerate(headers) if name not in {"status", "attempts"}]
    rows = {}
    for number, line in enumerate(lines[2:], 3):
        if not line.strip():
            continue
        values = [field.strip() for field in line.split("|")]
        if len(values) != len(headers):
            raise ValueError(f"wrong column count in {path}:{number}")
        case = values[headers.index("case_directory")]
        if not case or case in rows:
            raise ValueError(f"empty or duplicate case directory in {path}:{number}")
        rows[case] = [values[index] for index in keep]
    return [headers[index] for index in keep], rows


def final_frames_by_case(frames, rows):
    """Match each case to its last available snapshot, before applying frame filters."""
    final = {}
    for frame in frames:
        directory = frame.parent
        case = directory.name
        if case not in rows:
            metadata = directory / "case.json"
            if metadata.is_file():
                case = json.loads(metadata.read_text()).get("case_name")
            if case not in rows:
                raise ValueError(f"no case table entry for {directory.name}")
        previous = final.get(case)
        if previous and previous.parent != directory:
            raise ValueError(f"multiple directories match case table entry {case}")
        final[case] = frame  # Frames arrive in ascending numeric order per directory.
    return final


def aggregate_properties(directory, materials):
    """Read masses and material fractions, excluding remainder and total rows."""
    table = directory / "aggregates.txt"
    if not table.is_file():
        return None
    rows = [line.split() for line in table.read_text().splitlines() if line.strip()]
    log = (directory / "aggregates.log").read_text()
    second = re.search(r"(?m)^# 2nd-largest aggregate:\s*\n#[^\n]*\n(\S+)", log)
    if second is None:
        raise ValueError(f"missing second-aggregate label in {directory}/aggregates.log")
    has_second = float(second[1]) > 0
    if not 2 + has_second <= len(rows) <= 3 + has_second:
        raise ValueError(f"unexpected aggregate row count in {table}")
    values = []
    for row in rows[:1 + has_second]:
        if len(row) != 8 + materials or not all(math.isfinite(float(value)) for value in row):
            raise ValueError(f"invalid aggregate data in {table}")
        values.extend([row[6], *row[8:]])
    return values + (["-"] * (1 + materials) if not has_second else [])


def write_results_table(output, headers, cases, final_frames, materials):
    """Write one row per input case, marking unavailable aggregate data with dashes."""
    fields = ["mass_kg", *(f"mat{i}_frac" for i in range(materials))]
    headers = [*headers, "final_frame", *(
        f"{rank}_{field}" for rank in ("largest", "second") for field in fields
    )]
    rows = []
    available = 0
    for case, parameters in cases.items():
        frame = final_frames.get(case)
        properties = (
            aggregate_properties(output / frame.parent.name / frame.stem, materials)
            if frame else None
        )
        available += properties is not None
        rows.append([*parameters, frame.stem if frame else "-",
                     *(properties if properties is not None else ["-"] * (2 * len(fields)))])
    widths = [max(map(len, column)) for column in zip(headers, *rows)]
    path = output / "results_table.txt"
    with path.open("x") as table:
        def render(row):
            return " | ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()

        table.write(render(headers) + "\n")
        table.write("-+-".join("-" * width for width in widths) + "\n")
        for row in rows:
            table.write(render(row) + "\n")
    print(f"Final aggregates available for {available}/{len(cases)} cases: {path}")


def discover_frames(sweep, selected_cases):
    """List snapshots by case and numeric frame, including unprocessed frames."""
    cases = {path.name: path for path in sorted(sweep.glob("case_*")) if path.is_dir()}
    if not cases:
        raise ValueError(f"no case directories found in {sweep}")
    unknown = set(selected_cases) - cases.keys()
    if unknown:
        raise ValueError(f"unknown case directories: {', '.join(sorted(unknown))}")
    frames = []
    for name in sorted(set(selected_cases) or cases.keys()):
        matches = []
        for path in cases[name].iterdir():
            match = FRAME_NAME.fullmatch(path.name)
            if match and path.is_file():
                number = int(match[1])
                matches.append((number, path))
        if not matches:
            print(f"Warning: no matching HDF5 frames in {name}", file=sys.stderr)
        frames.extend(path for _, path in sorted(matches))
    if not frames:
        raise ValueError("no matching impact.<frame>.h5 files found")
    return frames


def process_frame(frame, destination, tools, materials):
    destination.mkdir(parents=True)
    snapshot = os.path.relpath(frame, destination)
    # The C tool stores the input argument in a fixed 256-byte buffer.
    if len(os.fsencode(snapshot)) >= 256:
        raise ValueError("HDF5 input path is too long; choose a closer output directory")
    commands = (
        ("fragments", [tools / "fast_identify_fragments", "-H", "-i", snapshot,
                       "-m", materials], "fragments.frag"),
        ("aggregates", [tools / "calc_aggregates", "-t", "-f", "fragments.frag",
                        "-n", materials], "aggregates.txt"),
    )
    for stage, command, output in commands:
        partial = destination / f"{output}.partial"
        with (destination / f"{stage}.log").open("w") as stdout, (
            destination / f"{stage}.error"
        ).open("w") as stderr:
            result = subprocess.run(
                [*command, "-o", partial.name], cwd=destination, stdout=stdout, stderr=stderr,
            )
        if result.returncode:
            raise ValueError(f"{stage} exited with status {result.returncode}; see {destination}")
        with partial.open() as data:
            if not any(line.strip() and not line.startswith("#") for line in data):
                raise ValueError(f"{stage} produced no data rows; see {destination}")
        partial.rename(destination / output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweep", type=Path, help="sweep directory containing case_* directories")
    parser.add_argument("--output-dir", type=Path,
                        help="results directory (default: SWEEP/postprocessing); must be new except with --table-only")
    parser.add_argument("--tools-dir", type=Path, default=TOOLS_DIR, help="directory with both built tools")
    parser.add_argument("--materials", choices=("2", "3", "4"), default="2",
                        help="number of material slots (default: 2 for iron/basalt)")
    parser.add_argument("--case", action="append", default=[], help="case directory name; repeat to select several")
    parser.add_argument("--frame", type=int, action="append", default=[], help="frame number; repeat to select several")
    parser.add_argument("--dry-run", action="store_true", help="list matching snapshots without writing output")
    parser.add_argument("--table-only", action="store_true", help="write the table from existing postprocessing outputs")
    args = parser.parse_args(argv)
    if args.table_only and (args.dry_run or args.frame):
        parser.error("--table-only cannot be combined with --dry-run or --frame")
    sweep = args.sweep.resolve()
    output = (args.output_dir or sweep / "postprocessing").resolve()
    tools = args.tools_dir.resolve()
    try:
        all_frames = discover_frames(sweep, args.case)
        frames = [frame for frame in all_frames
                  if not args.frame or int(FRAME_NAME.fullmatch(frame.name)[1]) in args.frame]
        if not frames:
            raise ValueError("no matching impact.<frame>.h5 files found")
        if args.dry_run:
            for frame in frames:
                print(frame.relative_to(sweep))
            print(f"{len(frames)} frame(s) selected")
            return 0
        case_table = sweep / "case_table.txt"
        table_data = None
        if case_table.is_file():
            headers, cases = read_case_table(case_table)
            table_data = headers, cases, final_frames_by_case(all_frames, cases), int(args.materials)
        elif args.table_only:
            raise ValueError(f"missing case table: {case_table}")
        else:
            print(f"Warning: no case table at {case_table}; no results table will be written", file=sys.stderr)
        if args.table_only:
            write_results_table(output, *table_data)
            return 0
        for executable in ("fast_identify_fragments", "calc_aggregates"):
            tool = tools / executable
            if not tool.is_file() or not os.access(tool, os.X_OK):
                raise ValueError(f"missing executable: {tool}; build the postprocessing tools first")
        output.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    failures = 0
    for index, frame in enumerate(frames, 1):
        case = frame.parent.name
        print(f"[{index}/{len(frames)}] {case}/{frame.name}", flush=True)
        try:
            process_frame(frame, output / case / frame.stem, tools, args.materials)
        except (OSError, ValueError) as error:
            failures += 1
            print(f"Failed {case}/{frame.name}: {error}", file=sys.stderr)
    print(f"{len(frames) - failures}/{len(frames)} frames succeeded; results: {output}")
    if table_data is not None:
        try:
            write_results_table(output, *table_data)
        except (OSError, ValueError) as error:
            print(f"Error writing results table: {error}", file=sys.stderr)
            return 1
    return int(failures != 0)


if __name__ == "__main__":
    sys.exit(main())
