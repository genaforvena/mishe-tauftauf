from mishe_tauftauf import roles


def test_roles_are_the_charters_that_exist(tmp_path):
    (tmp_path / "charters").mkdir()
    for name in ("genome", "docs", "health"):
        (tmp_path / "charters" / f"{name}.md").write_text("c")
    (tmp_path / "charters" / "notes.txt").write_text("not a role")
    assert roles.declared(tmp_path) == frozenset({"genome", "docs", "health"})


def test_no_charters_directory_means_no_roles(tmp_path):
    assert roles.declared(tmp_path) == frozenset()


def test_install_defaults_copies_packaged_charters_and_never_overwrites(tmp_path):
    installed = roles.install_defaults(tmp_path)
    assert set(installed) == set(roles.DEFAULT) and roles.declared(tmp_path) == frozenset(roles.DEFAULT)
    (tmp_path / "charters" / "genome.md").write_text("site-customized")
    assert roles.install_defaults(tmp_path) == []
    assert (tmp_path / "charters" / "genome.md").read_text() == "site-customized"
