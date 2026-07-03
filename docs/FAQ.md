# Netwright FAQ

**Does Netwright touch real hardware?**
No. Netwright is a design and static-validation tool. It does not push config to
switches or routers, does not discover live networks, and does not send packets.
It produces a project file, a diagram, and a `vlan_policy.json` you can use
elsewhere.

**Is this a network simulator / emulator?**
No. There is no live protocol behavior (no STP, VTP, or routing). Validation is
static analysis of the design you draw. Any future "simulation" feature is
estimation, clearly labeled as such.

**What if I don't have an Anthropic API key?**
Everything except the AI *Propose* feature works: drawing the topology, editing
VLANs and ACLs, validation, export, and a deterministic templated *Explain*. Set
`ANTHROPIC_API_KEY` to enable the assistant.

**Where is my API key stored?**
It is read from the `ANTHROPIC_API_KEY` environment variable (or, optionally, a
plaintext field in `~/.netwright/settings.json`, which is git-ignored). It is
never written into a `.netwright` project file, autosave, undo history, or log.

**Can the AI break my design?**
The AI only proposes changes. Netwright re-validates every proposed op, shows you
a diff, and applies nothing until you approve. Approved batches apply as one undo
step, so any change is reversible.

**How does this relate to SecureLink?**
Netwright is a portfolio sibling. It shares conventions and exports a
`vlan_policy.json` in the exact shape SecureLink's VLAN guard reads, so a design
here can drive policy enforcement there.
