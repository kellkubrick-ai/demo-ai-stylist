from ai_stylist.cli import parser


def test_full_catalog_import_is_explicit():
    arguments = ["import", "catalog.xml", "--mapping", "mapping.json"]
    assert parser().parse_args(arguments).all_groups is False
    assert parser().parse_args([*arguments, "--all-groups"]).all_groups is True
