"""Test the C fragment finder and the HDF5 sweep-to-results workflow.

Requires h5py, a C compiler and HDF5 development libraries. Set
HDF5_CFLAGS/HDF5_LIBS when HDF5 is not available through pkg-config.
"""

import math
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from scripts import postprocess_sweep

try:
    import h5py
except ImportError:
    h5py = None


SOURCE = Path(__file__).resolve().parents[1] / "src/fast_identify_fragments_and_calc_aggregates"


@unittest.skipIf(h5py is None, "fragment integration tests require h5py")
class FragmentPostprocessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.build.cleanup)
        cls.executable = Path(cls.build.name) / "fast_identify_fragments"
        flags = []
        for name, option in (("HDF5_CFLAGS", "--cflags"), ("HDF5_LIBS", "--libs")):
            value = os.environ.get(name)
            if value is None:
                value = subprocess.check_output(["pkg-config", option, "hdf5"], text=True)
            flags.extend(shlex.split(value))
        subprocess.run(
            shlex.split(os.environ.get("CC", "cc"))
            + [str(SOURCE / "fast_identify_fragments.c"), "-O1", "-g"]
            + shlex.split(os.environ.get("FRAGMENT_TEST_CFLAGS", ""))
            + flags + ["-lm", "-o", str(cls.executable)],
            check=True, capture_output=True, text=True,
        )

    def run_finder(self, lengths, *, hdf5=False, legacy=False, ignore=None,
                   materials=None, positions=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / ("input.h5" if hdf5 else "input.txt")
            output = root / "fragments.txt"
            count = len(positions) if positions is not None else len(lengths)
            if positions is None:
                positions = [[x, 0, 0] for x in [0, 1.5, 3, 10][:count]]
            materials = materials if materials is not None else [0] * count
            if hdf5:
                with h5py.File(source, "w") as handle:
                    handle["x"] = positions
                    handle["v"] = [[0.0, 0.0, 0.0]] * count
                    handle["m"] = [1.0] * count
                    handle["rho"] = [3000.0] * count
                    handle["e"] = [1e5] * count
                    handle["hsml" if legacy else "sml"] = lengths
                    handle["material_type"] = materials
            else:
                source.write_text("".join(
                    " ".join(map(str, [*position, 0, 0, 0, 1, 3000, 1e5,
                                       length, 0, material])) + "\n"
                    for position, length, material in zip(positions, lengths, materials)
                ))
            command = [str(self.executable), "-i", str(source), "-o", str(output), "-m", "1"]
            if hdf5:
                command.append("-H")
            if legacy:
                command.append("-L")
            if ignore is not None:
                command.extend(["-I", str(ignore)])
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            rows = [list(map(float, line.split())) for line in output.read_text().splitlines()
                    if line and not line.startswith("#")] if output.exists() else []
            return completed, rows

    def test_mean_links_same_fragments_as_constant_mean_in_both_formats(self):
        for hdf5, legacy in ((False, False), (True, False), (True, True)):
            with self.subTest(hdf5=hdf5, legacy=legacy):
                result, rows = self.run_finder([1, 3, 3, 1], hdf5=hdf5, legacy=legacy)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("mean = 2; min = 1; max = 3; particles = 4", result.stdout)
                self.assertEqual([row[6] for row in rows], [3, 1])
                constant, expected = self.run_finder([2] * 4, hdf5=hdf5, legacy=legacy)
                self.assertEqual(constant.returncode, 0, constant.stderr)
                self.assertEqual(rows, expected)

    def test_excluded_material_does_not_affect_mean(self):
        for hdf5 in (False, True):
            with self.subTest(hdf5=hdf5):
                result, rows = self.run_finder(
                    [math.nan, 1, 3, 3, 1], hdf5=hdf5, ignore=99,
                    materials=[99, 0, 0, 0, 0],
                    positions=[[20, 0, 0], [0, 0, 0], [1.5, 0, 0], [3, 0, 0], [10, 0, 0]],
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("mean = 2; min = 1; max = 3; particles = 4", result.stdout)
                self.assertEqual([row[6] for row in rows], [3, 1])

    def test_invalid_smoothing_lengths_fail(self):
        for hdf5 in (False, True):
            for value in (0, -1, math.nan, math.inf):
                with self.subTest(hdf5=hdf5, value=value):
                    result, _ = self.run_finder([1, value, 1, 1], hdf5=hdf5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("finite and positive", result.stderr)

    def test_empty_and_all_excluded_inputs_fail(self):
        for hdf5 in (False, True):
            with self.subTest(hdf5=hdf5):
                result, _ = self.run_finder([], hdf5=hdf5)
                self.assertIn("No particles remain", result.stderr)
                self.assertNotEqual(result.returncode, 0)
                result, _ = self.run_finder([1] * 4, hdf5=hdf5, materials=[99] * 4, ignore=99)
                self.assertIn("No particles remain", result.stderr)
                self.assertNotEqual(result.returncode, 0)

    def test_hdf5_smoothing_length_count_is_checked(self):
        result, _ = self.run_finder([1, 2, 3], hdf5=True,
                                    positions=[[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one value per particle", result.stderr)

    def test_neighbour_capacity_boundary(self):
        for count in (251, 252):
            with self.subTest(count=count):
                result, rows = self.run_finder(
                    [100] * count, hdf5=True,
                    positions=[[i * 0.01, 0, 0] for i in range(count)],
                )
                if count == 251:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual([row[6] for row in rows], [251])
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Maximum number of interaction partner (250) exceeded", result.stderr)

    def test_sweep_runs_both_tools_and_preserves_failed_frame_logs(self):
        tools = self.executable.parent
        subprocess.run(
            shlex.split(os.environ.get("CC", "cc"))
            + [str(SOURCE / "calc_aggregates.c"), "-lm", "-o", str(tools / "calc_aggregates")],
            check=True, capture_output=True, text=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            sweep = Path(directory)
            case = sweep / "case_00001_test"
            case.mkdir()
            (case / "case.json").write_text('{"case_name": "case_00001_original"}')
            (sweep / "case_table.txt").write_text(
                "status | attempts | case_directory | gamma\n"
                "-------+----------+----------------+------\n"
                "complete | 1 | case_00001_original | 1\n"
                "pending | 0 | case_00002_missing | 0.5\n"
            )
            for name, positions, velocity in (
                ("impact.0000.h5", [0, 0.5, 1, 1.5], [0, 0, 0, 0]),
                ("impact.0010.h5", [0, 0.5, 10, 10.5], [0, 0, 1, 1]),
            ):
                with h5py.File(case / name, "w") as handle:
                    handle["x"] = [[x, 0, 0] for x in positions]
                    handle["v"] = [[v, 0, 0] for v in velocity]
                    for dataset, values in {
                        "m": [1.0] * 4, "rho": [3000.0] * 4, "e": [1e5] * 4,
                        "sml": [1, 3, 3, 1], "material_type": [1] * 4,
                    }.items():
                        handle[dataset] = values
            (case / "impact.0002.h5").write_bytes(b"incomplete snapshot")
            (case / "impact.0000").touch()
            (case / "unrelated.h5").touch()
            self.assertEqual(
                [path.name for path in postprocess_sweep.discover_frames(sweep, [])],
                ["impact.0000.h5", "impact.0002.h5", "impact.0010.h5"],
            )
            self.assertEqual(postprocess_sweep.main([str(sweep), "--tools-dir", str(tools)]), 1)
            results = sweep / "postprocessing" / case.name
            for frame, masses in (("impact.0000", [4, 4]), ("impact.0010", [2, 2, 4])):
                output = results / frame
                rows = (output / "aggregates.txt").read_text().splitlines()
                self.assertEqual([float(row.split()[6]) for row in rows], masses)
                self.assertIn("mean = 2", (output / "fragments.log").read_text())
                self.assertIn("largest aggregate", (output / "aggregates.log").read_text())
            failed = results / "impact.0002"
            self.assertTrue((failed / "fragments.error").stat().st_size)
            self.assertFalse((failed / "aggregates.txt").exists())
            self.assertEqual(
                postprocess_sweep.aggregate_properties(results / "impact.0000", 2)[3:],
                ["-"] * 3,
            )
            table = sweep / "postprocessing" / "results_table.txt"
            lines = table.read_text().splitlines()
            headers = [field.strip() for field in lines[0].split("|")]
            final = dict(zip(headers, (value.strip() for value in lines[2].split("|"))))
            self.assertNotIn("status", headers)
            self.assertNotIn("attempts", headers)
            self.assertEqual(headers[2:], [
                "final_frame", "largest_mass_kg", "largest_mat0_frac", "largest_mat1_frac",
                "second_mass_kg", "second_mat0_frac", "second_mat1_frac",
            ])
            self.assertEqual(final["case_directory"], "case_00001_original")
            self.assertEqual(final["final_frame"], "impact.0010")
            self.assertEqual(float(final["largest_mass_kg"]), 2)
            self.assertEqual(float(final["second_mass_kg"]), 2)
            self.assertEqual(float(final["second_mat1_frac"]), 1)
            self.assertEqual(lines[3].split("|")[2].strip(), "-")
            self.assertEqual(postprocess_sweep.main([str(sweep), "--tools-dir", str(tools)]), 1)
            table.rename(table.with_suffix(".previous"))
            # A later snapshot exists, but has no postprocessing result yet.
            (case / "impact.0011.h5").touch()
            self.assertEqual(postprocess_sweep.main([
                str(sweep), "--table-only", "--tools-dir", str(sweep / "unavailable-tools"),
            ]), 0)
            rebuilt = table.read_text().splitlines()
            final = dict(zip(headers, (value.strip() for value in rebuilt[2].split("|"))))
            self.assertEqual(final["final_frame"], "impact.0011")
            self.assertEqual(final["largest_mass_kg"], "-")

    def test_out_of_range_material_id_fails_before_fragment_composition(self):
        result, _ = self.run_finder([1] * 4, hdf5=True, materials=[0, 1, 0, 0])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Material ID 1 is outside the slots", result.stderr)


if __name__ == "__main__":
    unittest.main()
