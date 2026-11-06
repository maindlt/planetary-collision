# Sweep postprocessing

Run this after the GPU jobs have finished writing their snapshots. Both
postprocessing tools run on the CPU. Build them first:

```sh
make -C src/fast_identify_fragments_and_calc_aggregates
```

See the tools' [QuickStart](../src/fast_identify_fragments_and_calc_aggregates/QuickStart.md)
for HDF5 build flags and VSC-5 module setup. The driver itself uses only Python's
standard library.

From the repository root, preview the files and then process them:

```sh
python3 scripts/postprocess_sweep.py runs/moon_mars_cases --dry-run
python3 scripts/postprocess_sweep.py runs/moon_mars_cases
```

The driver finds `impact.<frame>.h5` files directly inside each `case_*`
directory and visits frames in numeric order. It includes frame zero if an HDF5
snapshot exists for it. ASCII files and unrelated HDF5 files are ignored.
Cases without matching snapshots are reported and skipped; an empty selection
is an error. The original `case_table.txt` remains the parameter-to-directory
lookup. The driver does not modify it or any simulation output.

For a first look, choose one case and one frame and use a separate output
directory:

```sh
python3 scripts/postprocess_sweep.py runs/moon_mars_cases \
    --case CASE_DIRECTORY_NAME --frame 10 --output-dir runs/postprocessing_preview
```

Replace `CASE_DIRECTORY_NAME` with a name from the case table. Both selection
options may be repeated. Frame numbers do not need leading zeroes. Use
`--tools-dir` if your compiled tools live outside their source directory.
The default material count is two slots (iron ID 0, basalt ID 1), including
the pure-basalt runs. Use `--materials 3` or `--materials 4` for layouts
requiring more slots.

Each frame's files go under `SWEEP/postprocessing/CASE/impact.FRAME/` by default:

- `fragments.frag`: spatially connected fragments, sorted by mass.
- `fragments.log`: mean, minimum and maximum smoothing length and particle count.
- `aggregates.txt`: the raw numeric table from `calc_aggregates -t -o`.
- `aggregates.log`: labelled aggregate masses, compositions, positions, velocities
  and relative kinetics. Start here to see what the aggregate calculation found.
- `fragments.error` and `aggregates.error`: diagnostics from the two tools.

The aggregate table contains position, velocity, mass, a relative-mass column,
and material fractions. Rows are the largest aggregate, the second-largest if
present, the remaining material if present, and the overall total. Use the
labelled log to distinguish the optional rows. The upstream tool leaves the
relative-mass column at zero; use the mass column for inspection and do not
interpret that zero as a measured mass fraction.

The driver runs frames sequentially. A failed frame is reported and other frames
continue; the final exit status is nonzero if any selected frame failed. The
logs and any `.partial` outputs are retained for diagnosis. Existing output
directories are refused to avoid overwriting earlier analysis. To retry only
affected frames, use the selection options with a new `--output-dir`.

## Final aggregate results table

If `SWEEP/case_table.txt` is present, the driver writes `results_table.txt` in
the postprocessing output directory. It preserves the case-table order and
input columns except for `status` and `attempts`, then adds the final frame
name and the largest and second-largest aggregates' masses (kg) and material
fractions. Positions and velocities remain in the per-frame outputs.

The final frame is the highest numbered HDF5 snapshot available for that
case; it does not imply the simulation reached its intended end time. Missing
results (including a failed or unselected final frame) and absent second
aggregates are marked `-`. An earlier result is never substituted. A renamed
case directory can be matched using its original `case_name` in `case.json`.

For previously processed frames, use `--table-only` with the same sweep and
output directory. This reads the existing outputs without running the C tools.
For the default output directory:

```sh
python3 scripts/postprocess_sweep.py runs/moon_mars_cases --table-only
```

This still needs the source case directories and HDF5 filenames to identify
each final frame. Use the same `--materials` value as in the original analysis.
If a table already exists, rename it before rebuilding; it is not overwritten.
Without `case_table.txt`, frame processing still works, but the summary table
is not produced and `--table-only` reports an error.
