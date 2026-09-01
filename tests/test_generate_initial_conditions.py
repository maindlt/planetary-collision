import math
from pathlib import Path
import unittest

from scripts.generate_initial_conditions import (
    ConfigurationError,
    _collision_timescale,
    _planned_miluphcuda,
    _validate_miluphcuda_config,
    expand_cases,
    expand_parameter,
    render_case_table,
    render_spheres_input,
)


def constant(value):
    return {"mode": "constant", "value": value}


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
        self.assertIn("case_directory", lines[0])
        self.assertIn("m_tot_kg", lines[0])
        self.assertIn("impact_angle_deg", lines[0])
        self.assertIn("case_00001_abc123", lines[2])
        self.assertIn("1.886e+21", lines[2])
        self.assertIn("100000", lines[2])


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
        planned = _planned_miluphcuda(configuration, Path("/tmp/case"), 100.0)
        self.assertEqual(planned["status"], "planned_not_executed")
        self.assertEqual(planned["command"][0], "miluphcuda_future")
        self.assertIn("10", planned["command"])
        self.assertIn("/tmp/case/impact.0000", planned["command"])
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


if __name__ == "__main__":
    unittest.main()
