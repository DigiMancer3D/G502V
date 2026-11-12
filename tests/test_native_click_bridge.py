"""Regression tests for native L1/R1, synthetic remaps and all soloPAW modes."""
import json
import tempfile
import unittest
from pathlib import Path

from evdev_global_input import EvdevGlobalInput, parse_proc_bus_input_devices
from input_remapper_auto import InputRemapperAutoMapper


class ThreeProfileTests(unittest.TestCase):
    @staticmethod
    def record(code, symbol, second=None):
        raw = [{"type": 1, "code": code}]
        if second is not None:
            raw.append({"type": 1, "code": second})
        return {"input_combination": raw, "output_symbol": symbol}

    def load(self, name, mappings):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = Path(self.temp.name) / (name + '.json')
        p.write_text(json.dumps(mappings))
        a = InputRemapperAutoMapper()
        self.assertTrue(a.load_profile_path(p))
        return a

    def test_megabonk_unmapped_main_buttons_and_direct_keys(self):
        # Provided Megabonk v1.1: both main buttons absent (no remapping).
        m = self.load("soloPAW-Megabonk", [
            self.record(47, "disable"),
            self.record(17, "w"),
            self.record(31, "s"),
            self.record(47, "Escape", second=32),
        ])
        self.assertEqual(m.unmapped_native_click_codes(), frozenset({272, 273}))
        self.assertEqual(m.resolve("w")[0].visuals, ("G5",))
        self.assertEqual(m.resolve("escape")[0].visuals,
                         ("Trigger (G-Switch)", "M3 (Tilt Right)"))

    def test_desktop_mapped_main_buttons_and_combo_clicks(self):
        # Provided Linux Desktop v1.5 maps BTN_LEFT/RIGHT with G-Shift.
        m = self.load("soloPAW-LinuxDesktop", [
            self.record(272, "if_eq($soloPAW_shift_v15,1,hold_keys(Control_L,BTN_LEFT),hold_keys(BTN_LEFT))"),
            self.record(273, "if_eq($soloPAW_shift_v15,1,hold_keys(BTN_MIDDLE),hold_keys(BTN_RIGHT))"),
            self.record(57, "if_eq($soloPAW_shift_v15,1,hold_keys(Shift_L,BTN_LEFT),hold_keys(Shift_L))"),
        ])
        self.assertEqual(m.unmapped_native_click_codes(), frozenset())
        self.assertEqual({a.visuals for a in m.resolve("button.left")},
                         {("L1",), ("L1", "Trigger (G-Switch)"),
                          ("G8", "Trigger (G-Switch)")})
        self.assertEqual(m.resolve("button.right")[0].visuals, ("R1",))
        self.assertEqual(m.resolve("button.middle")[0].visuals,
                         ("R1", "Trigger (G-Switch)"))

    def test_cyberpunk_mapped_clicks_in_complex_macro(self):
        m = self.load("soloPAW-Cyberpunk", [
            self.record(272, "if_eq($soloPAW_shift,1,hold_keys(x),hold_keys(BTN_LEFT))"),
            self.record(273, "if_eq($soloPAW_shift,1,if_tap(if_tap(key(n),key(BTN_MIDDLE),300),hold_keys(BTN_MIDDLE),650),hold_keys(BTN_RIGHT))"),
        ])
        self.assertEqual(m.unmapped_native_click_codes(), frozenset())
        self.assertEqual(m.resolve("button.left")[0].visuals, ("L1",))
        self.assertEqual(m.resolve("button.middle")[0].visuals,
                         ("R1", "Trigger (G-Switch)"))
        self.assertEqual(m.resolve("x")[0].visuals,
                         ("L1", "Trigger (G-Switch)"))

    def test_main_click_partial_remapping(self):
        m = self.load("only-left-mapped", [self.record(272, "BTN_MIDDLE")])
        self.assertEqual(m.unmapped_native_click_codes(), frozenset({273}))

    def test_macro_key_sequence_parsed(self):
        m = self.load("sysrq-sequence", [self.record(1,
            "key_down(KEY_LEFTMETA).key_down(Shift_L).key(KEY_SYSRQ).key_up(Shift_L).key_up(KEY_LEFTMETA)")])
        self.assertEqual(m.resolve("sysrq")[0].visuals, ("M3",))
        self.assertEqual(m.resolve("leftmeta")[0].visuals, ("M3",))


class BridgePipelineTests(unittest.TestCase):
    class Event:
        def __init__(self, code, value, event_type=1):
            self.code = code
            self.value = value
            self.type = event_type

    class Dev:
        def __init__(self, fd): self.fd = fd

    class E:
        EV_SYN = 0; EV_KEY = 1; EV_REL = 2
        SYN_REPORT = 0
        REL_X = 0; REL_Y = 1; REL_HWHEEL = 6; REL_WHEEL = 8
        bytype = {1: {272: "BTN_LEFT", 273: "BTN_RIGHT", 17: "KEY_W"}}

    def setUp(self):
        self.visuals = []
        self.outputs = []
        self.b = EvdevGlobalInput(
            on_input=lambda key, down: self.outputs.append((key, down)),
            on_scroll=lambda *_: None,
            on_move=lambda *_: None,
            on_button_visual=lambda visual, down, origin:
                self.visuals.append((visual, down, origin)),
        )
        self.b._ecodes = self.E
        self.raw = self.Dev(101)
        self.virtual = self.Dev(102)
        self.hardware = self.Dev(103)
        self.b._physical_g502_fds.add(self.raw.fd)
        self.b._physical_mouse_fds.add(self.raw.fd)
        self.b._virtual_mouse_fds.add(self.virtual.fd)

    def test_megabonk_native_clicks_from_virtual_mouse(self):
        self.b.set_native_click_passthrough({272, 273})
        for code, button in [(272, 'L1'), (273, 'R1')]:
            self.b._process(self.virtual, self.Event(code, 1))
            self.b._process(self.virtual, self.Event(code, 0))
            self.assertEqual([x[:2] for x in self.visuals[-2:]],
                             [(button, True), (button, False)])
        self.assertEqual(self.outputs, [])

    def test_cyberpunk_desktop_virtual_outputs_keep_original_mapping_path(self):
        self.b.set_native_click_passthrough(set())
        self.b._process(self.virtual, self.Event(272, 1))
        self.b._process(self.virtual, self.Event(272, 0))
        self.assertEqual(self.outputs,
                         [('button.left', True), ('button.left', False)])
        self.assertEqual(self.visuals, [])

    def test_dual_raw_and_virtual_press_merge_without_early_unlight(self):
        self.b.set_native_click_passthrough({272, 273})
        self.b._process(self.virtual, self.Event(272, 1))
        self.b._process(self.raw, self.Event(272, 1))
        self.b._process(self.virtual, self.Event(272, 0))
        self.assertEqual(len(self.visuals), 1)
        self.b._process(self.raw, self.Event(272, 0))
        self.assertEqual([x[:2] for x in self.visuals],
                         [('L1', True), ('L1', False)])

    def test_hardware_identity_restricted_to_logitech_hero(self):
        ok = EvdevGlobalInput.is_g502_hardware_identity
        self.assertTrue(ok('Logitech G502 HERO Gaming Mouse',
                           'usb-0000:00:14.0-1/input0', 0x046d, 0xc08b))
        self.assertFalse(ok('Logitech G502 HERO Gaming Mouse',
                            'input-remapper/usb', 0x046d, 0xc08b))
        self.assertFalse(ok('Other Gaming Mouse', 'usb', 0x046d, 0xc08b))
        self.assertFalse(ok('Logitech G502 HERO Gaming Mouse', 'usb', 0x046d, 0xc08a))

    def test_hardware_and_proxy_clicks_merge(self):
        self.b._physical_g502_fds.add(self.hardware.fd)
        self.b._hardware_g502_fds.add(self.hardware.fd)
        self.b._process(self.hardware, self.Event(273, 1))
        self.b._process(self.raw, self.Event(273, 1))
        self.b._process(self.hardware, self.Event(273, 0))
        self.b._process(self.raw, self.Event(273, 0))
        self.assertEqual([x[:2] for x in self.visuals],
                         [('R1', True), ('R1', False)])

    def test_switch_to_desktop_releases_native_mouse_holds(self):
        self.b.set_native_click_passthrough({272, 273})
        self.b._process(self.virtual, self.Event(272, 1))
        self.b.set_native_click_passthrough(set())
        self.assertEqual([x[:2] for x in self.visuals],
                         [('L1', True), ('L1', False)])

    def test_synthetic_native_from_other_keyboard_not_mislabeled(self):
        self.b.set_native_click_passthrough({272, 273})
        other = self.Dev(999)
        self.b._process(other, self.Event(272, 1))
        self.assertEqual(self.visuals, [])
        self.assertEqual(self.outputs, [('button.left', True)])

    def test_proc_parser_hardware_identity_is_exact(self):
        data = ('I: Bus=0003 Vendor=046d Product=c08b Version=0111\n'
                'N: Name="Logitech G502 HERO Gaming Mouse"\n'
                'P: Phys=usb-0000:00:14.0-1/input0\n'
                'H: Handlers=mouse0 event12')
        row = parse_proc_bus_input_devices(data)[0]
        self.assertEqual((row.vendor, row.product), (0x046d, 0xc08b))
        self.assertEqual(row.event_paths, ('/dev/input/event12',))


if __name__ == '__main__':
    unittest.main()
