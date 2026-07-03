from core.model import Device, Link, Port, Topology


def test_sync_builds_one_item_per_device(qapp):
    from ui.canvas import TopologyScene

    t = Topology()
    t.add_device(Device("d1", "D1", "switch", x=0, y=0))
    scene = TopologyScene(t)
    scene.sync()
    assert len(scene.device_items) == 1
    assert "d1" in scene.device_items


def test_moving_a_node_reroutes_its_links(qapp):
    from ui.canvas import TopologyScene

    t = Topology()
    t.add_device(Device("d1", "D1", "switch", x=0, y=0, ports=[Port("p", "p")]))
    t.add_device(Device("d2", "D2", "switch", x=100, y=0, ports=[Port("p", "p")]))
    t.add_link(Link("ln1", "d1", "p", "d2", "p"))
    scene = TopologyScene(t)
    scene.sync()

    before = scene.link_items["ln1"].path().boundingRect()
    scene.move_device("d1", 0, 300)
    after = scene.link_items["ln1"].path().boundingRect()

    assert before != after  # the link followed the moved node
    assert t.devices["d1"].y == 300  # model coords stayed in sync
