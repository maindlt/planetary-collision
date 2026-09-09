from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

from scripts.generate_initial_conditions import (
    ConfigurationError,
    MILUPHCUDA_SCRIPT_NAME,
    SPHERES_INI_OUTPUT_MODE,
    _collision_timescale,
    _planned_miluphcuda,
    _sweep_lock,
    _validate_miluphcuda_config,
    _write_miluphcuda_script,
    execute,
    expand_cases,
    expand_parameter,
    main,
    render_case_table,
    render_spheres_input,
)


def constant(value):
    return {"mode": "constant", "value": value}


def write_execution_fixture(root: Path, impact_angles: list[float]) -> tuple[Path, Path]:
    source = root / "spheres_ini_source"
    (source / "SEAGen").mkdir(parents=True, exist_ok=True)
    (source / "run_SEAGen.py").write_text("")
    (source / "SEAGen" / "seagen.py").write_text("")
    executable = source / "spheres_ini"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "Path('impact.0000').write_text("
        "'0 0 0 0 0 0 1e21 3000 1e5 0\\n1 0 0 0 0 0 1e21 3000 1e5 1\\n')\n"
        "Path('projectile.structure').write_text('1 0\\n')\n"
        "Path('target.structure').write_text('1 0\\n')\n"
        "print('projectile: N_des = 1 N = 1')\n"
        "print('target: N_des = 1 N = 1')\n"
        "print('projectile: desired: R = 1')\n"
        "print('  actual/final: R = 1')\n"
        "print('target: desired: R = 1')\n"
        "print('  actual/final: R = 1')\n"
        "print('collision timescale (R_p+R_t)/|v_imp| = 100 sec')\n"
    )
    executable.chmod(0o755)
    material = root / "material.cfg"
    material.write_text("material fixture\n")
    output = root / "sweep"
    config_path = root / "sweep.json"
    configuration = {
        "paths": {
            "spheres_ini_executable": str(executable),
            "spheres_ini_source": str(source),
            "material_file": str(material),
            "output_directory": str(output),
        },
        "execution": {
            "max_cases": 20,
            "miluphcuda": {
                "enabled": False,
                "executable": "miluphcuda",
                "arguments": ["-f", "{impact_file}", "-m", "{material_file}"],
                "n_frames": 10,
            },
        },
        "parameters": {
            "m_tot_kg": constant(2e21),
            "gamma": constant(1),
            "zeta_iron": constant(0.3),
            "v_imp_over_v_esc": constant(1),
            "impact_angle_deg": {"mode": "list", "values": impact_angles},
            "f_i": constant(5),
            "f_t": constant(50),
            "n_tot": constant(100),
        },
    }
    config_path.write_text(json.dumps(configuration))
    return config_path, output


class ParameterExpansionTests(unittest.TestCase):
    def test_all_modes(self):
        self.assertEqual(expand_parameter("x", constant(3)), [3.0])
        self.assertEqual(expand_parameter("x", {"mode": "list", "values": [1, 2]}), [1.0, 2.0])
        self.assertEqual(
            expand_parameter("x", {"mode": "linear", "minimum": 1, "maximum": 3, "count": 3}),
            [1.0, 2.0, 3.0],
        )
        logarithmic = expand_parameter(
            "x", {"mode": "log", "minimum": 1, "maximum": 8, "count": 4}
        )
        self.assertEqual(logarithmic[0], 1.0)
        self.assertEqual(logarithmic[-1], 8.0)
        self.assertTrue(math.isclose(logarithmic[1], 2.0))
        self.assertTrue(math.isclose(logarithmic[2], 4.0))

    def test_cartesian_product_and_fixed_ft(self):
        parameters = {
            "m_tot_kg": constant(1e21),
            "gamma": {"mode": "list", "values": [0.1, 1]},
            "zeta_iron": constant(0.25),
            "v_imp_over_v_esc": constant(1.5),
            "impact_angle_deg": {"mode": "list", "values": [0, 30, 60]},
            "f_i": constant(5),
            "f_t": constant(50),
            "n_tot": constant(10000),
        }
        cases = expand_cases(parameters)
        self.assertEqual(len(cases), 6)
        parameters["f_t"] = constant(49)
        with self.assertRaisesRegex(ConfigurationError, "fixed at 50"):
            expand_cases(parameters)


class InputRenderingTests(unittest.TestCase):
    def test_output_mode_is_hydro_with_density(self):
        self.assertEqual(SPHERES_INI_OUTPUT_MODE, 0)

    def test_hydro_material_and_damage_settings(self):
        case = {
            "m_tot_kg": 1.2e21,
            "gamma": 0.5,
            "zeta_iron": 0.3,
            "v_imp_over_v_esc": 2.0,
            "impact_angle_deg": 45.0,
            "f_i": 5.0,
            "f_t": 50.0,
            "n_tot": 10000.0,
        }
        rendered = render_spheres_input(case)
        self.assertIn("M_proj = 4e+20\n", rendered)
        self.assertIn("mantle_proj = 0.7\n", rendered)
        self.assertIn("mantle_target = 0.7\n", rendered)
        self.assertIn("core_mat = Iron\n", rendered)
        self.assertIn("mantle_mat = BasaltNakamura\n", rendered)
        self.assertIn("shell_proj = 0\n", rendered)
        self.assertIn("weibull_core = 0\n", rendered)
        self.assertNotIn("\n\n", rendered)

    def test_case_table_maps_parameters_to_case_directories(self):
        records = [
            {
                "case_name": "case_00001_abc123",
                "derived": {"n_tot_actual": 11234},
                "parameters": {
                    "m_tot_kg": 1.886e21,
                    "gamma": 0.5,
                    "zeta_iron": 0.25,
                    "v_imp_over_v_esc": 1.5,
                    "impact_angle_deg": 30.0,
                    "f_i": 5.0,
                    "f_t": 50.0,
                    "n_tot": 100000.0,
                },
            }
        ]
        table = render_case_table(records)
        lines = table.splitlines()
        self.assertIn("status", lines[0])
        self.assertIn("attempts", lines[0])
        self.assertIn("case_directory", lines[0])
        self.assertIn("m_tot_kg", lines[0])
        self.assertIn("impact_angle_deg", lines[0])
        self.assertIn("n_tot_actual", lines[0])
        self.assertIn("case_00001_abc123", lines[2])
        self.assertIn("1.886e+21", lines[2])
        self.assertIn("100000", lines[2])
        self.assertTrue(lines[2].endswith("11234"))
        pending_record = {**records[0]}
        pending_record.pop("derived")
        self.assertTrue(render_case_table([pending_record]).splitlines()[2].endswith("-"))


class ExecutionMetadataTests(unittest.TestCase):
    def test_collision_timescale_comes_from_spheres_ini_output(self):
        stdout = "        collision timescale (R_p+R_t)/|v_imp| = 952.399 sec\n"
        self.assertEqual(_collision_timescale(stdout), 952.399)

    def test_planned_miluphcuda_command_is_never_enabled(self):
        configuration = {
            "enabled": False,
            "executable": "miluphcuda_future",
            "n_frames": 10,
            "arguments": [
                "-n", "{n_frames}", "-t", "{output_interval_s}",
                "-f", "{impact_file}", "-m", "{material_file}",
            ],
        }
        _validate_miluphcuda_config(configuration)
        planned = _planned_miluphcuda(configuration, 100.0)
        self.assertEqual(planned["status"], "planned_not_executed")
        self.assertEqual(planned["command"][0], "miluphcuda_future")
        self.assertIn("10", planned["command"])
        self.assertIn("impact.0000", planned["command"])
        self.assertNotIn("/tmp/case/impact.0000", planned["command"])
        self.assertEqual(planned["working_directory"], ".")
        self.assertEqual(planned["script_file"], MILUPHCUDA_SCRIPT_NAME)
        configuration["enabled"] = True
        with self.assertRaisesRegex(ConfigurationError, "must remain false"):
            _validate_miluphcuda_config(configuration)

    def test_unknown_planned_command_placeholder_is_rejected(self):
        configuration = {
            "enabled": False,
            "executable": "miluphcuda",
            "n_frames": 10,
            "arguments": ["{unsupported}"],
        }
        with self.assertRaisesRegex(ConfigurationError, "unknown.*placeholder"):
            _validate_miluphcuda_config(configuration)

    def test_writes_executable_portable_miluphcuda_script(self):
        configuration = {
            "enabled": False,
            "executable": "miluphcuda future",
            "n_frames": 10,
            "arguments": ["-f", "{impact_file}", "-m", "{material_file}"],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            case_directory = Path(temporary_directory)
            planned = _planned_miluphcuda(configuration, 100.0)
            script_path = _write_miluphcuda_script(case_directory, planned)
            self.assertEqual(script_path.name, "run_miluphcuda.sh")
            self.assertTrue(os.access(script_path, os.X_OK))
            self.assertEqual(
                script_path.read_text(),
                "#!/bin/sh\n"
                "cd \"$(dirname \"$0\")\" && exec 'miluphcuda future' "
                "-f impact.0000 -m material.cfg\n",
            )


class RestartExecutionTests(unittest.TestCase):
    def test_resume_preserves_complete_cases_and_extend_adds_only_new_cases(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path, output = write_execution_fixture(root, [0])
            self.assertEqual(execute(config_path, silent=True), 0)

            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["status_counts"], {"complete": 1})
            table = (output / "case_table.txt").read_text().splitlines()
            self.assertIn("n_tot_actual", table[0])
            self.assertTrue(table[2].endswith("2"))
            original_case = output / manifest["cases"][0]["case_name"]
            marker = original_case / "preserved.marker"
            marker.write_text("keep\n")

            self.assertEqual(
                execute(config_path, silent=True, restart_mode="resume"), 0
            )
            self.assertTrue(marker.is_file())

            config_path, _ = write_execution_fixture(root, [0, 30])
            self.assertEqual(
                execute(config_path, silent=True, restart_mode="extend"), 0
            )
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["case_count"], 2)
            self.assertEqual(manifest["status_counts"], {"complete": 2})
            self.assertTrue(marker.is_file())
            self.assertEqual(len(manifest["extensions"]), 1)

            with self.assertRaisesRegex(ConfigurationError, "strict superset"):
                execute(config_path, silent=True, restart_mode="extend")

    def test_resume_archives_and_recalculates_an_interrupted_case(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path, output = write_execution_fixture(root, [0])
            self.assertEqual(execute(config_path, silent=True), 0)
            manifest = json.loads((output / "manifest.json").read_text())
            case_directory = output / manifest["cases"][0]["case_name"]
            case_data = json.loads((case_directory / "case.json").read_text())
            case_data["status"] = "running"
            (case_directory / "case.json").write_text(json.dumps(case_data))

            self.assertEqual(
                execute(config_path, silent=True, restart_mode="resume"), 0
            )
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["cases"][0]["status"], "complete")
            self.assertEqual(manifest["cases"][0]["attempts"], 2)
            archived = list((output / "_incomplete_attempts").iterdir())
            self.assertEqual(len(archived), 1)

    def test_failed_case_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path, output = write_execution_fixture(root, [0])
            self.assertEqual(execute(config_path, silent=True), 0)
            manifest = json.loads((output / "manifest.json").read_text())
            case_directory = output / manifest["cases"][0]["case_name"]
            case_data = json.loads((case_directory / "case.json").read_text())
            case_data["status"] = "failed"
            case_data["return_code"] = 2
            (case_directory / "case.json").write_text(json.dumps(case_data))

            with self.assertRaisesRegex(RuntimeError, "failed case"):
                execute(config_path, silent=True, restart_mode="resume")
            self.assertEqual(
                execute(
                    config_path,
                    silent=True,
                    restart_mode="resume",
                    retry_failed=True,
                ),
                0,
            )
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["cases"][0]["status"], "complete")
            self.assertEqual(manifest["cases"][0]["attempts"], 2)

    def test_legacy_manifest_can_extend_using_its_case_table(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path, output = write_execution_fixture(root, [0])
            self.assertEqual(execute(config_path, silent=True), 0)
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest.pop("schema_version")
            manifest.pop("restart_signature")
            manifest.pop("plan_fingerprint")
            manifest_path.write_text(json.dumps(manifest))

            config_path, _ = write_execution_fixture(root, [0, 30])
            self.assertEqual(
                execute(config_path, silent=True, restart_mode="extend"), 0
            )
            migrated = json.loads(manifest_path.read_text())
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(migrated["case_count"], 2)
            self.assertIn("migrated_from_legacy_manifest_at", migrated)

    def test_legacy_density_free_sweep_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path, output = write_execution_fixture(root, [0])
            self.assertEqual(execute(config_path, silent=True), 0)
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest.pop("schema_version")
            manifest.pop("restart_signature")
            manifest["mode"]["spheres_ini_output_mode"] = 3
            manifest["mode"]["density_column"] = False
            manifest_path.write_text(json.dumps(manifest))

            with self.assertRaisesRegex(ConfigurationError, "incompatible"):
                execute(config_path, silent=True, restart_mode="resume")

    def test_sweep_lock_rejects_a_concurrent_generator(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path, output = write_execution_fixture(root, [0])
            self.assertEqual(execute(config_path, silent=True), 0)
            with _sweep_lock(output):
                with self.assertRaisesRegex(ConfigurationError, "another generator"):
                    execute(config_path, silent=True, restart_mode="resume")


class CommandLineTests(unittest.TestCase):
    def test_silent_suppresses_handled_error_output(self):
        stdout = StringIO()
        stderr = StringIO()
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_config = Path(temporary_directory) / "missing.json"
            with redirect_stdout(stdout), redirect_stderr(stderr):
                return_code = main([str(missing_config), "--silent"])

        self.assertEqual(return_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
