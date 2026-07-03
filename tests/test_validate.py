"""One positive + one negative case per validation code."""
from core.model import AclRule, Device, Link, Port, Subnet, Topology, Vlan
from core.policy import VlanPolicyEngine
from core.validate import validate


def codes(topology, policy=None):
    return {i.code for i in validate(topology, policy)}


def _switch(dev_id, *ports):
    return Device(dev_id, dev_id.upper(), "switch", ports=list(ports))


# ---- VLAN_OUT_OF_RANGE ----------------------------------------------------
def test_vlan_out_of_range_positive():
    t = Topology(vlans={5000: Vlan(5000, "Bad")})
    assert "VLAN_OUT_OF_RANGE" in codes(t)


def test_vlan_out_of_range_negative():
    t = Topology(vlans={10: Vlan(10, "Ok")})
    assert "VLAN_OUT_OF_RANGE" not in codes(t)


# ---- VLAN1_IN_USE ---------------------------------------------------------
def test_vlan1_in_use_positive():
    p = Port("g0/1", "g0/1", mode="access", access_vlan=1)
    t = Topology(devices={"s1": _switch("s1", p)}, vlans={1: Vlan(1, "default")})
    assert "VLAN1_IN_USE" in codes(t)


def test_vlan1_in_use_negative():
    p = Port("g0/1", "g0/1", mode="access", access_vlan=10)
    t = Topology(devices={"s1": _switch("s1", p)}, vlans={10: Vlan(10, "Sales")})
    assert "VLAN1_IN_USE" not in codes(t)


# ---- DUP_VLAN_ID ----------------------------------------------------------
def test_dup_vlan_id_positive():
    t = Topology(vlans={10: Vlan(10, "Sales"), 99: Vlan(10, "DupSales")})
    assert "DUP_VLAN_ID" in codes(t)


def test_dup_vlan_id_negative():
    t = Topology(vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")})
    assert "DUP_VLAN_ID" not in codes(t)


# ---- ACCESS_VLAN_UNDEFINED ------------------------------------------------
def test_access_vlan_undefined_positive():
    p = Port("g0/1", "g0/1", mode="access", access_vlan=99)
    t = Topology(devices={"s1": _switch("s1", p)}, vlans={10: Vlan(10, "Sales")})
    assert "ACCESS_VLAN_UNDEFINED" in codes(t)


def test_access_vlan_undefined_negative():
    p = Port("g0/1", "g0/1", mode="access", access_vlan=10)
    t = Topology(devices={"s1": _switch("s1", p)}, vlans={10: Vlan(10, "Sales")})
    assert "ACCESS_VLAN_UNDEFINED" not in codes(t)


# ---- TRUNK_VLAN_UNDEFINED -------------------------------------------------
def test_trunk_vlan_undefined_positive():
    p = Port("g0/1", "g0/1", mode="trunk", allowed_vlans=[99])
    t = Topology(devices={"s1": _switch("s1", p)}, vlans={10: Vlan(10, "Sales")})
    assert "TRUNK_VLAN_UNDEFINED" in codes(t)


def test_trunk_vlan_undefined_negative():
    p = Port("g0/1", "g0/1", mode="trunk", allowed_vlans=[10])
    t = Topology(devices={"s1": _switch("s1", p)}, vlans={10: Vlan(10, "Sales")})
    assert "TRUNK_VLAN_UNDEFINED" not in codes(t)


# ---- NATIVE_VLAN_MISMATCH -------------------------------------------------
def _trunk_pair(native_a, native_b):
    pa = Port("g0/1", "g0/1", mode="trunk", allowed_vlans=[10, 20], native_vlan=native_a)
    pb = Port("g0/1", "g0/1", mode="trunk", allowed_vlans=[10, 20], native_vlan=native_b)
    t = Topology(
        devices={"s1": _switch("s1", pa), "s2": _switch("s2", pb)},
        vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")},
    )
    t.add_link(Link("ln1", "s1", "g0/1", "s2", "g0/1", kind="trunk"))
    return t


def test_native_vlan_mismatch_positive():
    assert "NATIVE_VLAN_MISMATCH" in codes(_trunk_pair(10, 20))


def test_native_vlan_mismatch_negative():
    assert "NATIVE_VLAN_MISMATCH" not in codes(_trunk_pair(10, 10))


# ---- MODE_MISMATCH --------------------------------------------------------
def test_mode_mismatch_positive():
    pa = Port("g0/1", "g0/1", mode="access", access_vlan=10)
    pb = Port("g0/1", "g0/1", mode="trunk", allowed_vlans=[10])
    t = Topology(
        devices={"s1": _switch("s1", pa), "s2": _switch("s2", pb)},
        vlans={10: Vlan(10, "Sales")},
    )
    t.add_link(Link("ln1", "s1", "g0/1", "s2", "g0/1"))
    assert "MODE_MISMATCH" in codes(t)


def test_mode_mismatch_negative():
    pa = Port("g0/1", "g0/1", mode="access", access_vlan=10)
    pb = Port("g0/1", "g0/1", mode="access", access_vlan=10)
    t = Topology(
        devices={"s1": _switch("s1", pa), "s2": _switch("s2", pb)},
        vlans={10: Vlan(10, "Sales")},
    )
    t.add_link(Link("ln1", "s1", "g0/1", "s2", "g0/1"))
    assert "MODE_MISMATCH" not in codes(t)


# ---- SUBNET_OVERLAP -------------------------------------------------------
def test_subnet_overlap_positive():
    t = Topology(
        vlans={
            10: Vlan(10, "A", subnet="10.0.0.0/24"),
            20: Vlan(20, "B", subnet="10.0.0.0/25"),
        }
    )
    assert "SUBNET_OVERLAP" in codes(t)


def test_subnet_overlap_negative():
    t = Topology(
        vlans={
            10: Vlan(10, "A", subnet="10.0.10.0/24"),
            20: Vlan(20, "B", subnet="10.0.20.0/24"),
        }
    )
    assert "SUBNET_OVERLAP" not in codes(t)


# ---- HOST_BITS_SET --------------------------------------------------------
def test_host_bits_set_positive():
    t = Topology(vlans={10: Vlan(10, "A", subnet="10.0.0.1/24")})
    assert "HOST_BITS_SET" in codes(t)


def test_host_bits_set_negative():
    t = Topology(vlans={10: Vlan(10, "A", subnet="10.0.0.0/24")})
    assert "HOST_BITS_SET" not in codes(t)


def test_p2p_31_does_not_false_positive():
    t = Topology(vlans={10: Vlan(10, "P2P", subnet="10.0.0.0/31", gateway="10.0.0.1")})
    found = codes(t)
    assert "HOST_BITS_SET" not in found
    assert "GATEWAY_OUTSIDE_SUBNET" not in found


# ---- GATEWAY_OUTSIDE_SUBNET ----------------------------------------------
def test_gateway_outside_subnet_positive():
    t = Topology(vlans={10: Vlan(10, "A", subnet="10.0.0.0/24", gateway="10.0.99.1")})
    assert "GATEWAY_OUTSIDE_SUBNET" in codes(t)


def test_gateway_outside_subnet_negative():
    t = Topology(vlans={10: Vlan(10, "A", subnet="10.0.0.0/24", gateway="10.0.0.1")})
    assert "GATEWAY_OUTSIDE_SUBNET" not in codes(t)


# ---- DUP_IP ---------------------------------------------------------------
def test_dup_ip_positive():
    t = Topology(
        devices={
            "s1": Device("s1", "S1", "switch", mgmt_ip="10.0.0.5"),
            "s2": Device("s2", "S2", "switch", mgmt_ip="10.0.0.5"),
        }
    )
    assert "DUP_IP" in codes(t)


def test_dup_ip_negative():
    t = Topology(
        devices={
            "s1": Device("s1", "S1", "switch", mgmt_ip="10.0.0.5"),
            "s2": Device("s2", "S2", "switch", mgmt_ip="10.0.0.6"),
        }
    )
    assert "DUP_IP" not in codes(t)


# ---- DANGLING_LINK --------------------------------------------------------
def test_dangling_link_positive():
    t = Topology(devices={"s1": _switch("s1", Port("g0/1", "g0/1"))})
    t.add_link(Link("ln1", "s1", "g0/1", "ghost", "g0/9"))
    assert "DANGLING_LINK" in codes(t)


def test_dangling_link_negative(sample_topology):
    assert "DANGLING_LINK" not in codes(sample_topology)


# ---- PORT_DOUBLE_LINKED ---------------------------------------------------
def test_port_double_linked_positive():
    t = Topology(
        devices={
            "s1": _switch("s1", Port("g0/1", "g0/1")),
            "s2": _switch("s2", Port("g0/1", "g0/1")),
            "s3": _switch("s3", Port("g0/1", "g0/1")),
        }
    )
    t.add_link(Link("ln1", "s1", "g0/1", "s2", "g0/1"))
    t.add_link(Link("ln2", "s1", "g0/1", "s3", "g0/1"))  # reuses s1/g0/1
    assert "PORT_DOUBLE_LINKED" in codes(t)


def test_port_double_linked_negative(sample_topology):
    assert "PORT_DOUBLE_LINKED" not in codes(sample_topology)


# ---- ISOLATED_DEVICE ------------------------------------------------------
def test_isolated_device_positive():
    t = Topology(devices={"s1": _switch("s1", Port("g0/1", "g0/1"))})
    assert "ISOLATED_DEVICE" in codes(t)


def test_isolated_device_negative(sample_topology):
    assert "ISOLATED_DEVICE" not in codes(sample_topology)


# ---- ACL_REFERENCES_UNKNOWN_VLAN -----------------------------------------
def test_acl_unknown_vlan_positive():
    t = Topology(vlans={10: Vlan(10, "Sales")}, acls=[AclRule(10, 20, "permit")])
    assert "ACL_REFERENCES_UNKNOWN_VLAN" in codes(t)


def test_acl_unknown_vlan_negative():
    t = Topology(
        vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")},
        acls=[AclRule(10, 20, "permit")],
    )
    assert "ACL_REFERENCES_UNKNOWN_VLAN" not in codes(t)


# ---- ACL_CONTRADICTION ----------------------------------------------------
def test_acl_contradiction_positive():
    t = Topology(
        vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")},
        acls=[AclRule(10, 20, "permit"), AclRule(10, 20, "deny")],
    )
    assert "ACL_CONTRADICTION" in codes(t)


def test_acl_contradiction_negative():
    t = Topology(
        vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")},
        acls=[AclRule(10, 20, "permit")],
    )
    assert "ACL_CONTRADICTION" not in codes(t)


# ---- INTER_VLAN_DENIED ----------------------------------------------------
def test_inter_vlan_denied_positive():
    t = Topology(
        vlans={10: Vlan(10, "Sales"), 30: Vlan(30, "Guest")},
        acls=[AclRule(10, 30, "permit")],
    )
    policy = VlanPolicyEngine({10: [10, 20]})  # 10->30 denied
    assert "INTER_VLAN_DENIED" in codes(t, policy)


def test_inter_vlan_denied_negative():
    t = Topology(
        vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")},
        acls=[AclRule(10, 20, "permit")],
    )
    policy = VlanPolicyEngine({10: [10, 20]})  # 10->20 allowed
    assert "INTER_VLAN_DENIED" not in codes(t, policy)
