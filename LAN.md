# Run STAR on your team's LAN

Use a machine connected to the same office/home network as your teammates.
Keep it powered on while the app is needed. No Docker or internet-facing
deployment is required. The application uses HTTPS and individual accounts.

## 1. Choose the machine's private address

On the LAN machine, run `ip -4 -brief address` (Linux). Choose the actual
Ethernet/Wi-Fi address, for example `192.168.1.50`, not a Docker address or
`127.0.0.1`. Reserve that address in your router/DHCP setup if it should stay
stable. All commands below run from the `star-preparation-engine` directory.

## 2. Create accounts

```bash
./star-prep user-add team-admin --role admin --data-dir data
```

The command prompts for a password of at least 12 characters. Create a
separate account for each teammate using `--role preparer` (or `viewer` for
read-only access). Every account in this deployment can read its stored runs.

## 3. Set up a certificate

Use a certificate from your organization's trusted CA when available. Its
Subject Alternative Name must cover the IP or hostname teammates will use.
Keep the private key on the server only.

For a small local test, generate a self-signed certificate on the LAN machine.
**Replace the example IP below with that machine's real private IP.**

```bash
export STAR_LAN_IP=192.168.1.50
umask 077
mkdir -p data/tls
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 30 \
  -keyout data/tls/lan.key -out data/tls/lan.crt \
  -subj "/CN=$STAR_LAN_IP" -addext "subjectAltName=IP:$STAR_LAN_IP"
```

Generate once; do not overwrite an existing deployed certificate/key without
planning the rotation. Browsers will not automatically trust a self-signed
certificate. Install/trust `lan.crt` on each teammate's device using your
organization's certificate process. Share only the certificate, never
`lan.key`. Certificates and private keys under ignored `data/` stay out of Git.

## 4. Start LAN mode

```bash
./star-prep serve --lan --host "$STAR_LAN_IP" --data-dir data \
  --tls-cert data/tls/lan.crt --tls-key data/tls/lan.key
```

Teammates open **`https://192.168.1.50:8443`** (substitute the actual IP),
then sign in. HTTPS and sign-in are automatic in `--lan` mode. Port 8443 is
the LAN default; use `--port` to choose another. No process is started in the
background: keep the terminal running, and press Ctrl+C to stop.

The certificate example assumes Linux with OpenSSL available. If using an
organization-issued certificate, replace the two file paths in the serve
command. The existing HTTPS reverse-proxy deployment remains supported.

## Connectivity and troubleshooting

- The host must actually own the address supplied to `--host`. `--lan`
  rejects public IPs, loopback and wildcard binding. It accepts private IPv4,
  unique-local IPv6 and the `100.64.0.0/10` address space used by some VPNs.
- Allow inbound TCP 8443 **only from your team's LAN/VPN subnet** in the
  host firewall. No firewall rules are changed by this application. Do not
  configure router port forwarding for this LAN-only setup.
- Client isolation on guest Wi-Fi, different VLANs or VPN routing can prevent
  teammates from reaching the host. Verify routing with your network admin.
- Use `https://`, not `http://`. If the certificate/IP changes or the test
  certificate expires, issue and trust a replacement.
- An address-in-use error now identifies the occupied port. Stop the existing
  instance or choose another port; do not terminate unrelated services.
- A test from the server itself does not prove another device can reach it.
  Confirm sign-in from a second LAN device before sharing customer data.
- Local mode (`./star-prep serve`) still listens only on localhost:8080.

See [DEPLOYMENT.md](DEPLOYMENT.md) for backups, retention, roles and shared
storage. A publicly addressed remote server needs a private VPN interface
before it can use LAN mode; merely binding it to every interface does not
make it a LAN deployment.
