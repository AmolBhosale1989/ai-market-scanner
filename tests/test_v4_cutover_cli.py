from scanner.v4.cutover import CutoverController, SHADOW_MODE


def test_cutover_defaults_fail_closed():
    assert CutoverController("test_cutover").state()["mode"] == SHADOW_MODE
