from core.project import NetwrightProject


def test_save_load_identity(tmp_path, sample_topology):
    project = NetwrightProject(name="RT", topology=sample_topology)
    path = tmp_path / "p.netwright"
    project.save(path)
    loaded = NetwrightProject.load(path)
    assert loaded.topology.to_dict() == sample_topology.to_dict()
    assert loaded.name == "RT"


def test_save_is_atomic_no_tmp_left(tmp_path, sample_topology):
    project = NetwrightProject(name="RT", topology=sample_topology)
    path = tmp_path / "p.netwright"
    project.save(path)
    assert path.exists()
    assert not (tmp_path / "p.netwright.tmp").exists()
