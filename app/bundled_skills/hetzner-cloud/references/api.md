# Hetzner Cloud API: curl reference

Use when the script cannot run. Base URL `https://api.hetzner.cloud/v1`. JSON in and out.

Auth header without printing the token:

```bash
H="Authorization: Bearer $(cat ~/.config/hcloud-fuse/token)"   # or: Bearer $HCLOUD_TOKEN
J="Content-Type: application/json"
A=https://api.hetzner.cloud/v1
```

Never `echo` the token or `cat` the file on its own.

## Discover

```bash
curl -s -H "$H" "$A/locations"                                   # fsn1 nbg1 hel1 ash hil sin (Nov 2026)
curl -s -H "$H" "$A/images?type=system&status=available&architecture=x86"
curl -s -H "$H" "$A/server_types?name=cax11"                     # .locations[].available
curl -s -H "$H" "$A/pricing"                                     # currency, vat_rate, server_types[].prices[], primary_ips[]
curl -s -H "$H" "$A/ssh_keys"
curl -s -H "$H" -H "$J" -X POST "$A/ssh_keys" -d '{"name":"me","public_key":"ssh-ed25519 AAAA..."}'
```

Price for type T in location L: `/pricing` -> `server_types[name==T].prices[location==L].price_hourly.net`.
Add `primary_ips[type==ipv4].prices[location==L].price_hourly.net` if the server gets IPv4.
First check `/server_types?name=T` -> `locations[name==L].available == true`: listed types can be sold out.

List endpoints paginate: `?page=N&per_page=50`, follow `meta.pagination.next_page` until `null`.

## Create

```bash
curl -s -H "$H" -H "$J" -X POST "$A/servers" -d '{
  "name": "job-123",
  "server_type": "cpx02",
  "location": "hel1",
  "image": "ubuntu-24.04",
  "ssh_keys": [12345],
  "labels": {"managed-by": "fuse", "fuse-expires": "1795000000"},
  "start_after_create": true,
  "public_net": {"enable_ipv4": true, "enable_ipv6": true}
}'
```

Required: `name`, `server_type`, `image`. Optional: `location`, `ssh_keys` (ids), `user_data` (cloud-init string),
`labels`, `volumes`, `networks`, `firewalls` (`[{"firewall": id}]`), `placement_group`, `automount`.
Response: `server`, `action`, `next_actions`, `root_password` (set only when no SSH key is given).
Names: unique per project, RFC 1123 (letters, digits, `-`, `.`).
Always set a `fuse-expires` label (unix time) so `hcloud.py reap` can clean up.

## State

```bash
curl -s -H "$H" "$A/servers?label_selector=managed-by%3Dfuse"
curl -s -H "$H" "$A/servers?name=job-123"
curl -s -H "$H" "$A/servers/ID"          # .status: initializing starting running stopping off migrating rebuilding deleting unknown
curl -s -H "$H" "$A/actions/ACTION_ID"   # .status: running | success | error
```

Every mutation returns an `action`. Poll it every 2 s until it is not `running`.

## Manage

```bash
curl -s -H "$H" -X POST "$A/servers/ID/actions/poweron"
curl -s -H "$H" -X POST "$A/servers/ID/actions/shutdown"   # graceful (ACPI)
curl -s -H "$H" -X POST "$A/servers/ID/actions/poweroff"   # hard
curl -s -H "$H" -X POST "$A/servers/ID/actions/reboot"
curl -s -H "$H" -X POST "$A/servers/ID/actions/reset"      # hard
curl -s -H "$H" -H "$J" -X PUT "$A/servers/ID" -d '{"name":"new-name","labels":{"managed-by":"fuse"}}'
```

`PUT` changes only `name` and `labels`; the label set you send replaces the old one.

Rescale (server must be `off`):

```bash
curl -s -H "$H" -H "$J" -X POST "$A/servers/ID/actions/change_type" -d '{"server_type":"cax21","upgrade_disk":false}'
```

`upgrade_disk: true` grows the disk; disks never shrink, so the server can then only move to types with an equal or bigger disk.

## Delete

```bash
curl -s -H "$H" -X DELETE "$A/servers/ID"
```

Billing for the server stops at deletion. Auto-created Primary IPs are deleted with it; check `GET /primary_ips` for leftovers.

## Errors

Body: `{"error":{"code":"...","message":"..."}}`. `unauthorized` means a bad token. Read `message` for the rest.

Official docs: https://docs.hetzner.cloud/reference/cloud
