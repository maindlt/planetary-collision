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
    MASS_UNITS_KG,
    SPHERES_INI_FRAGMENTATION_OUTPUT_MODE,
    SPHERES_INI_NO_DENSITY_OUTPUT_MODE,
    SPHERES_INI_OUTPUT_MODE,
    SPHERES_INI_SOLID_OUTPUT_MODE,
    _collision_timescale,
    _planned_miluphcuda,
    _sweep_lock,
    _validate_miluphcuda_config,
    _write_miluphcuda_script,
    execute,
    expand_cases,
    expand_parameter,
    load_configuration,
    main,
    parse_case_list,
    render_case_table,
    render_spheres_input,
)


def constant(value):
    return {"mode": "constant", "value": value}


def write_execution_fixture(
    root: Path,
    impact_angles: list[float],
    generation: dict | None = None,
) -> tuple[Path, Path]:
    source = root / "spheres_ini_source"
    (source / "SEAGen").mkdir(parents=True, exist_ok=True)
    (source / "run_SEAGen.py").write_text("")
    (source / "SEAGen" / "seagen.py").write_text("")
    executable = source / "spheres_ini"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        "mode = int(sys.argv[sys.argv.index('-O') + 1])\n"
        "def row(x, material):\n"
        "    columns = [x, 0, 0, 0, 0, 0, '1e21']\n"
        "    if mode != 3:\n"
        "        columns.append(3000)\n"
        "    columns.extend(['1e5', material])\n"
        "    if mode == 2:\n"
        "        columns.extend([0, 0])\n"
        "    if mode in (1, 2):\n"
        "        columns.extend([0] * 9)\n"
        "    return ' '.join(map(str, columns))\n"
        "Path('impact.0000').write_text(row(0, 0) + '\\n' + row(1, 1) + '\\n')\n"
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
    if generation is not None:
        configuration["generation"] = generation
    config_path.write_text(json.dumps(configuration))
    return config_path, output


class ParameterExpansionTests(unittest.TestCase):
    def test_coreless_cases_in_both_input_formats(self):
        case = {
            "m_tot_kg": 2 * MASS_UNITS_KG["moon"], "gamma": 1,
            "zeta_iron": 0, "v_imp_over_v_esc": 1.625,
            "impact_angle_deg": 0, "f_i": 5, "f_t": 50, "n_tot": 1000,
        }
        parsed = parse_case_list([case])[0]
        self.assertEqual(expand_cases({name: constant(value) for name, value in case.items()}), [parsed])
        rendered = render_spheres_input(parsed)
        self.assertIn("mantle_proj = 1\n", rendered)
        self.assertIn("mantle_target = 1\n", rendered)
        self.assertIn("shell_proj = 0\n", rendered)
        for fraction in (-0.01, 1, 1.01):
            with self.subTest(fraction=fraction):
                with self.assertRaisesRegex(ConfigurationError, "zeta_iron"):
                    parse_case_list([{**case, "zeta_iron": fraction}])

    def test_explicit_cases_and_validation(self):
        case = {
            "m_tot_kg": {"value": 2, "unit": "earth"},
            "gamma": 0.1, "zeta_iron": 0.3, "v_imp_over_v_esc": 1.5,
            "impact_angle_deg": 30, "f_i": 5, "f_t": 50, "n_tot": 1000,
        }
        other = {**case, "m_tot_kg": 1e23, "impact_angle_deg": 45}
        parsed = parse_case_list([case, other])
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["m_tot_kg"], 2 * MASS_UNITS_KG["earth"])
        self.assertEqual(parsed[1]["m_tot_kg"], 1e23)
        self.assertEqual(case["m_tot_kg"], {"value": 2, "unit": "earth"})
        invalid = [
            [], {}, [None], [{**case, "extra": 1}],
            [{key: value for key, value in case.items() if key != "gamma"}],
            [{**case, "gamma": True}], [{**case, "gamma": float("nan")}],
            [{**case, "impact_angle_deg": 91}], [{**case, "f_t": 49}],
            [{**case, "n_tot": 100.5}],
            [{**case, "m_tot_kg": {"mode": "list", "values": [1, 2]}}],
            [{**case, "m_tot_kg": {"value": 1, "unit": "solar"}}],
            [case, {**case, "m_tot_kg": 2 * MASS_UNITS_KG["earth"]}],
        ]
        for specifications in invalid:
            with self.subTest(specifications=specifications):
                with self.assertRaises(ConfigurationError):
                    parse_case_list(specifications)

    def test_mass_units_normalize_all_grid_modes_to_kilograms(self):
        specifications = [
            constant(2),
            {"mode": "list", "values": [1, 2, 4]},
            {"mode": "linear", "minimum": 1, "maximum": 4, "count": 4},
            {"mode": "log", "minimum": 1, "maximum": 8, "count": 5},
        ]
        for unit, scale in MASS_UNITS_KG.items():
            for specification in specifications:
                with self.subTest(unit=unit, mode=specification["mode"]):
                    in_kg = dict(specification)
                    for key in ("value", "minimum", "maximum"):
                        if key in in_kg:
                            in_kg[key] *= scale
                    if "values" in in_kg:
                        in_kg["values"] = [value * scale for value in in_kg["values"]]
                    self.assertEqual(
                        expand_parameter("m_tot_kg", {**specification, "unit": unit}),
                        expand_parameter("m_tot_kg", in_kg),
                    )

    def test_mass_units_reject_invalid_units_and_overflow(self):
        for unit in ("Moon", "solar", None, [], 1):
            with self.subTest(unit=unit):
                with self.assertRaisesRegex(ConfigurationError, "unit must be"):
                    expand_parameter("m_tot_kg", {**constant(1), "unit": unit})
        with self.assertRaisesRegex(ConfigurationError, "only supported"):
            expand_parameter("gamma", {**constant(1), "unit": "earth"})
        with self.assertRaisesRegex(ConfigurationError, "finite"):
            expand_parameter("m_tot_kg", {**constant(1e300), "unit": "earth"})

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
        self.assertEqual(SPHERES_INI_SOLID_OUTPUT_MODE, 1)
        self.assertEqual(SPHERES_INI_FRAGMENTATION_OUTPUT_MODE, 2)
        self.assertEqual(SPHERES_INI_NO_DENSITY_OUTPUT_MODE, 3)

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
    def test_case_list_resume_extend_and_grid_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path, output = write_execution_fixture(Path(temporary_directory), [0, 30])
            configuration = json.loads(config_path.read_text())
            original_cases = expand_cases(configuration["parameters"])
            del configuration["parameters"]
            configuration["cases"] = original_cases
            config_path.write_text(json.dumps(configuration))
            self.assertEqual(execute(config_path, silent=True), 0)
            initial = json.loads((output / "manifest.json").read_text())["cases"]
            configuration["cases"] = list(reversed(original_cases))
            config_path.write_text(json.dumps(configuration))
            self.assertEqual(execute(config_path, silent=True, restart_mode="resume"), 0)
            configuration["cases"].append({**original_cases[0], "impact_angle_deg": 45})
            config_path.write_text(json.dumps(configuration))
            self.assertEqual(execute(config_path, silent=True, restart_mode="extend"), 0)
            extended = json.loads((output / "manifest.json").read_text())["cases"]
            self.assertEqual(len(extended), 3)
            self.assertEqual([case["case_name"] for case in extended[:2]],
                             [case["case_name"] for case in initial])
            self.assertTrue(all(case["attempts"] == 1 for case in extended))
            configuration["cases"] = [original_cases[0], {**original_cases[0], "impact_angle_deg": 60}]
            config_path.write_text(json.dumps(configuration))
            with self.assertRaises(ConfigurationError):
                execute(config_path, silent=True, restart_mode="extend")
            del configuration["cases"]
            configuration["parameters"] = {
                name: constant(value) for name, value in original_cases[0].items()
            }
            configuration["parameters"]["impact_angle_deg"] = {"mode": "list", "values": [0, 30, 45]}
            config_path.write_text(json.dumps(configuration))
            self.assertEqual(execute(config_path, silent=True, restart_mode="resume"), 0)

    def test_case_list_root_schema_and_case_limit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path, _ = write_execution_fixture(Path(temporary_directory), [0, 30])
            configuration = json.loads(config_path.read_text())
            cases = expand_cases(configuration["parameters"])
            for selection in ({}, {"cases": cases, "parameters": configuration["parameters"]}):
                invalid = {key: value for key, value in configuration.items() if key != "parameters"}
                invalid.update(selection)
                config_path.write_text(json.dumps(invalid))
                with self.assertRaisesRegex(ConfigurationError, "exactly one"):
                    load_configuration(config_path)
            del configuration["parameters"]
            configuration["cases"] = cases
            configuration["execution"]["max_cases"] = 1
            config_path.write_text(json.dumps(configuration))
            with self.assertRaisesRegex(ConfigurationError, "exceeding max_cases"):
                load_configuration(config_path)

    def test_resume_accepts_equivalent_mass_units(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path, output = write_execution_fixture(Path(temporary_directory), [0])
            configuration = json.loads(config_path.read_text())
            configuration["parameters"]["m_tot_kg"] = {
                **constant(2), "unit": "earth"
            }
            config_path.write_text(json.dumps(configuration))
            self.assertEqual(execute(config_path, silent=True), 0)
            original = json.loads((output / "manifest.json").read_text())["cases"][0]
            self.assertEqual(original["parameters"]["m_tot_kg"], 2 * MASS_UNITS_KG["earth"])
            configuration["parameters"]["m_tot_kg"] = constant(2 * MASS_UNITS_KG["earth"])
            config_path.write_text(json.dumps(configuration))
            self.assertEqual(execute(config_path, silent=True, restart_mode="resume"), 0)
            resumed = json.loads((output / "manifest.json").read_text())["cases"][0]
            self.assertEqual(resumed["case_name"], original["case_name"])
            self.assertEqual(resumed["attempts"], 1)

    def test_generation_rejects_invalid_physics_combinations(self):
        invalid_settings = (
            {
                "mode": "hydro",
                "fragmentation": True,
                "density_column": True,
            },
            {
                "mode": "solid",
                "fragmentation": False,
                "density_column": False,
            },
        )
        for generation in invalid_settings:
            with self.subTest(generation=generation):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    config_path, _ = write_execution_fixture(
                        Path(temporary_directory), [0], generation
                    )
                    with self.assertRaises(ConfigurationError):
                        execute(config_path, silent=True)

    def test_json_no_density_column_uses_mode_three_and_is_restart_compatible(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path, output = write_execution_fixture(
                root,
                [0],
                {
                    "mode": "hydro",
                    "fragmentation": False,
                    "density_column": False,
                },
            )
            self.assertEqual(execute(config_path, silent=True), 0)
            manifest = json.loads((output / "manifest.json").read_text())
            case = manifest["cases"][0]
            self.assertEqual(
                manifest["generation"],
                {
                    "mode": "hydro",
                    "fragmentation": False,
                    "density_column": False,
                },
            )
            self.assertEqual(manifest["mode"]["spheres_ini_output_mode"], 3)
            self.assertFalse(manifest["mode"]["density_column"])
            self.assertEqual(case["command"][case["command"].index("-O") + 1], "3")
            particle_row = (
                output / case["case_name"] / "impact.0000"
            ).read_text().splitlines()[0]
            self.assertEqual(len(particle_row.split()), 9)

            configuration = json.loads(config_path.read_text())
            configuration["generation"]["density_column"] = True
            config_path.write_text(json.dumps(configuration))
            with self.assertRaisesRegex(ConfigurationError, "incompatible"):
                execute(config_path, silent=True, restart_mode="resume")
            configuration["generation"]["density_column"] = False
            config_path.write_text(json.dumps(configuration))
            self.assertEqual(
                execute(
                    config_path,
                    silent=True,
                    restart_mode="resume",
                ),
                0,
            )

    def test_solid_modes_select_stress_and_fragmentation_outputs(self):
        modes = (
            (False, SPHERES_INI_SOLID_OUTPUT_MODE, 19, "weibull_mantle = 0"),
            (True, SPHERES_INI_FRAGMENTATION_OUTPUT_MODE, 21, "weibull_mantle = 1"),
        )
        for fragmentation, output_mode, column_count, weibull_setting in modes:
            with self.subTest(fragmentation=fragmentation):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    config_path, output = write_execution_fixture(
                        root,
                        [0],
                        {
                            "mode": "solid",
                            "fragmentation": fragmentation,
                            "density_column": True,
                        },
                    )
                    self.assertEqual(execute(config_path, silent=True), 0)
                    manifest = json.loads((output / "manifest.json").read_text())
                    case = manifest["cases"][0]
                    self.assertEqual(
                        manifest["mode"]["spheres_ini_output_mode"], output_mode
                    )
                    self.assertTrue(manifest["mode"]["solid_mechanics"])
                    self.assertEqual(
                        manifest["mode"]["fragmentation_damage"], fragmentation
                    )
                    case_directory = output / case["case_name"]
                    particle_row = (
                        (case_directory / "impact.0000")
                        .read_text()
                        .splitlines()[0]
                    )
                    self.assertEqual(len(particle_row.split()), column_count)
                    self.assertIn(
                        weibull_setting,
                        (case_directory / "spheres_ini.input").read_text(),
                    )

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

    def test_legacy_output_format_mismatch_is_rejected(self):
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
