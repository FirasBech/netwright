# Netwright User Manual

Netwright is a desktop tool for designing network topologies and VLANs visually,
with a Claude assistant that turns plain-language intent into validated config.
This manual walks through building a design, the AI assistant, validation, and
export.

## 1. Building a topology

1. **Add devices.** Drag a device kind (switch, router, firewall, host, server,
   access point) from the left palette onto the canvas, or use the CLI
   (`netwright add-device --kind switch --name Core-SW`).
2. **Draw links.** Switch to Link mode (`L`), then drag from one device's port to
   another's. A link joins two *ports*; access vs. trunk styling is shown.
3. **Move and arrange.** Drag nodes; they snap to the grid. Links follow.

## 2. VLANs, subnets, and trunks

- **Create a VLAN** in the Properties panel (project view) or
  `netwright vlan create --id 10 --vlan-name Sales`. Give it a color and an
  optional subnet (CIDR) + gateway.
- **Assign an access port** to a VLAN: select the port and pick its access VLAN,
  or `netwright set-port --device acc1 --port Fa0/1 --access-vlan 10`.
- **Configure a trunk** by setting the port mode to trunk and choosing the
  allowed VLANs and the native VLAN.

## 3. Inter-VLAN policy (ACLs)

Inter-VLAN traffic is **deny-by-default**. Permit a pair with an ACL rule
(`netwright acl add --src 10 --dst 20`) or the ACL matrix in the Properties
panel. Netwright exports this as a `vlan_policy.json` that SecureLink's runtime
guard consumes unchanged.

## 4. The AI assistant

Set `ANTHROPIC_API_KEY`, then describe a change in the AI panel, e.g.
*"Isolate Guest from Sales and Engineering."* The assistant proposes a batch of
edits; Netwright validates them, shows a diff (green = add, amber = change,
red = delete), and applies the approved batch as a single undo step. The model
proposes — the deterministic validator decides. Without a key, design,
validation, export, and a templated **Explain** still work.

## 5. Validation

`Validate` (or `netwright validate`) runs the deterministic engine and lists
issues by severity. Click an issue to highlight the offending device or link.

| Code | Meaning |
| --- | --- |
| `VLAN_OUT_OF_RANGE` | VLAN id not in 1–4094 |
| `VLAN1_IN_USE` | VLAN 1 used for user/native traffic |
| `DUP_VLAN_ID` | A VLAN id defined more than once |
| `ACCESS_VLAN_UNDEFINED` | Access port on a VLAN that isn't defined |
| `TRUNK_VLAN_UNDEFINED` | Trunk allows a VLAN that isn't defined |
| `NATIVE_VLAN_MISMATCH` | Trunk ends disagree on the native VLAN |
| `MODE_MISMATCH` | Access port linked to a trunk port |
| `SUBNET_OVERLAP` | Two VLAN subnets overlap |
| `HOST_BITS_SET` | CIDR has host bits set |
| `GATEWAY_OUTSIDE_SUBNET` | Gateway not a host in its subnet |
| `DUP_IP` | Same IP assigned twice |
| `DANGLING_LINK` | Link references a missing device/port |
| `PORT_DOUBLE_LINKED` | One port used by two links |
| `ISOLATED_DEVICE` | Device with no links |
| `ACL_REFERENCES_UNKNOWN_VLAN` | ACL references an undefined VLAN |
| `ACL_CONTRADICTION` | Permit and deny for the same pair |
| `INTER_VLAN_DENIED` | Design permits a pair the policy map denies |

## 6. Reachability

Ask whether one device can reach another under the current design:
`netwright reach --src h1 --dst h2`. Same-VLAN endpoints are reachable if the
VLAN-carrying link graph connects them; different-VLAN endpoints need an explicit
permit (deny-by-default). This is **static analysis**, not a packet simulator.

## 7. Export & import

- **Project** — `netwright export --format json --out project.json`
- **Policy** — `netwright export --format policy --out vlan_policy.json`
  (SecureLink-compatible); import one back with
  `netwright import-policy --file vlan_policy.json`.
- **Diagram** — `--format svg` (text) or `--format png` (from the dashboard).
- **Device config** — `--format ios --device <id>` emits a **SIMULATED**
  Cisco-IOS-style config; review before use.

## 8. Themes

Click **Theme** in the toolbar to cycle dark → light → high-contrast. The choice
is saved to `~/.netwright/settings.json` and restored on the next launch.

## 9. Discovery from LLDP/CDP

You don't draw an existing network by hand — import it. Run the neighbor command
on each device and save its output to a file (one file per device):

- **Huawei:** `display lldp neighbor` (or `display lldp neighbor brief`)
- **Cisco:** `show cdp neighbors detail` (or `show lldp neighbors detail`)

Then **File → Discover from LLDP/CDP…** (or `netwright discover CoreSW.txt
Access1.txt … --out net.netwright`). Netwright parses each neighbor's name,
connected interfaces, **model/description**, management IP, and role (switch /
router / server / AP / firewall from the advertised capabilities), builds the
devices and links, and merges the per-device views so reciprocal A↔B entries
become one link. This is offline — Netwright reads the text you provide and never
connects to your devices.

### Live discovery over SSH (optional)

If you'd rather not paste text, Netwright can fetch the neighbor tables itself.
Install the transport (`pip install netmiko`), write a JSON inventory of the
devices you administer:

```json
[
  { "host": "10.0.0.1", "username": "admin", "device_type": "huawei" },
  { "host": "10.0.0.254", "username": "admin", "device_type": "cisco_ios" }
]
```

then **File → Live discover (SSH)…** (it confirms authorization first) or
`netwright live-discover --inventory inv.json --out net.netwright --authorized`.
Netwright logs in, runs the platform's neighbor command, and pipes the output
through the same parsers as the offline path. Passwords come from the inventory
or the `NETFORGE_SSH_PASSWORD` environment variable. Unreachable devices are
reported and skipped, not fatal. **Authorized use only** — run this solely
against devices you own or administer.

### Live discovery over SNMP (optional)

When SSH is locked down but SNMP read is open, use SNMP instead
(`pip install pysnmp`). It walks the **LLDP-MIB** remote-systems table (read-only)
and builds the same map. Inventory entries take a community string:

```json
[ { "host": "10.0.0.1", "community": "public", "version": "2c" } ]
```

**File → Live discover (SNMP)…** or `netwright snmp-discover --inventory
snmp-inv.json --out net.netwright --authorized`. Community strings can also come
from `NETFORGE_SNMP_COMMUNITY`. Same authorization rules apply.

## 10. Autosave & recovery

While you have unsaved changes, Netwright writes an autosave snapshot
(`<project>.netwright.autosave`) every 60 seconds. Every manual save first rotates
the previous file to `<project>.netwright.bak`, then writes atomically. If Netwright
finds an autosave newer than the project when you open it, it offers to recover.
