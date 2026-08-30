import math
import unittest

from scripts.generate_initial_conditions import (
    ConfigurationError,
    expand_cases,
    expand_parameter,
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


if __name__ == "__main__":
    unittest.main()
