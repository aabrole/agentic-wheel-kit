# Run it 24/7 on a free Oracle Cloud server

Your computer only trades while it's awake. A free Oracle Cloud server runs the kit around the clock. This takes about 20 minutes the first time.

## 1. Create the Oracle Cloud account

1. Go to https://www.oracle.com/cloud/free/ and sign up for the **Free Tier**.
2. Oracle asks for an email, a phone number and a credit card. The card is for identity checks; **Always Free** resources aren't charged.
3. Pick a **home region** close to you. You can't change it later.

## 2. Create the server

1. In the Oracle console: **Compute → Instances → Create instance**.
2. **Image:** Canonical Ubuntu **24.04**.
3. **Shape:** Ampere **VM.Standard.A1.Flex** with 1 OCPU and 6 GB memory (marked "Always Free eligible").
   If Oracle says it's out of capacity, try another availability domain, or pick **VM.Standard.E2.1.Micro** (also free, slower).
4. **Networking:** let it create a new virtual cloud network with a **public subnet**, and keep **Assign a public IPv4 address** on.
5. **SSH keys:** choose **Generate a key pair for me** and **download the private key**. Keep that file safe; it's how you log in.
6. Click **Create**. When it's running, copy its **Public IP address**.

## 3. Open the web ports

The dashboard is served over HTTPS, so ports 80 and 443 have to be open.

1. On the instance page, click the **subnet** link, then its **Security List**.
2. **Add Ingress Rules**:
   - Source CIDR `0.0.0.0/0`, IP protocol **TCP**, destination port range `80,443`.
3. Save. (The install script opens the same ports in the server's own firewall.)

## 4. Log in to the server

On a Mac (Terminal) or Windows (PowerShell):

```sh
chmod 600 ~/Downloads/ssh-key-*.key        # Mac/Linux only
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_PUBLIC_IP
```

Type `yes` the first time it asks about the fingerprint.

## Install the kit

On the server:

```
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/aabrole/agentic-wheel-kit.git
cd agentic-wheel-kit
bash deploy/install.sh
```

The script prints your control panel address, a login and a password. The password is shown once, so save it.

## Connect your broker

```
cd ~/wheel-kit
nano .env                                   # set BROKER and, for Alpaca, your keys
.venv/bin/python -m wheel.agent connect     # Robinhood only
.venv/bin/python -m wheel.agent read        # first read of your account
```

Open the control panel address in your browser. You should see your own positions.

## Turn on the schedule

```
sudo systemctl enable --now wheel-cycle.timer
```

Twice each trading day (09:45 and 15:15 Eastern) the server reads your account, checks whether sent orders filled, messages you on Telegram if you set that up, and passes approved orders to your broker.

Approved orders are only passed on when `WHEEL_SEND=1` is in `.env`. It ships as 0. Read `DISCLAIMER.md` before you change it.

To stop everything from sending: set `WHEEL_SEND=0`, or `sudo systemctl disable --now wheel-cycle.timer`.

## Update later

```
cd ~/agentic-wheel-kit && git pull && bash deploy/install.sh
```

Your settings, login and history live in `~/wheel-kit/.env` and `~/wheel-kit/state/` and survive updates.

## Useful commands

- `systemctl list-timers 'wheel-*'` shows the schedule.
- `journalctl -u wheel-cycle -n 50` shows the last run.
- `~/wheel-kit/.venv/bin/python -m wheel.agent status` shows a one-line summary.
