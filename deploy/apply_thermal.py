import os
import paramiko
from pathlib import Path

# Laduj z .env
_env_path = Path(__file__).parent.parent / '.env'
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                os.environ.setdefault(key.strip(), value.strip())

HOST = os.environ.get('PI_HOST', '192.168.1.87')
USER = os.environ.get('PI_USER', 'pi')
PASS = os.environ.get('PI_PASS', '')

if not PASS:
    print("Blad: PI_PASS nie ustawiony w .env")
    exit(1)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS)
print("Connected!")

# 1. Check temp
_, out, _ = ssh.exec_command('vcgencmd measure_temp')
print(f"Temperature: {out.read().decode().strip()}")

# 2. Check existing config
_, out, _ = ssh.exec_command('grep -E "fan|arm_freq|gpu_mem" /boot/firmware/config.txt 2>/dev/null || grep -E "fan|arm_freq|gpu_mem" /boot/config.txt 2>/dev/null')
config = out.read().decode().strip()
print(f"Current config: {config or '(none)'}")

# 3. Determine config file
_, out, _ = ssh.exec_command('test -f /boot/firmware/config.txt && echo firmware || echo boot')
config_file = '/boot/firmware/config.txt' if 'firmware' in out.read().decode() else '/boot/config.txt'
print(f"Config file: {config_file}")

# 4. Apply thermal protection
changes = []
if 'arm_freq' not in config:
    ssh.exec_command(f'sudo bash -c "echo arm_freq=1500 >> {config_file}"')[1].channel.recv_exit_status()
    changes.append("CPU throttled to 1.5GHz (from 2.4GHz)")

if 'gpu_mem' not in config:
    ssh.exec_command(f'sudo bash -c "echo gpu_mem=64 >> {config_file}"')[1].channel.recv_exit_status()
    changes.append("GPU memory reduced to 64MB")

if 'gpio-fan' not in config:
    ssh.exec_command(f'sudo bash -c "echo dtoverlay=gpio-fan,gpiopin=14,temp=50000 >> {config_file}"')[1].channel.recv_exit_status()
    changes.append("Fan trigger set to 50°C")

if changes:
    print("\nApplied:")
    for c in changes:
        print(f"  OK: {c}")

    # Reboot
    print("\nRebooting Pi...")
    ssh.exec_command('sudo reboot')
    print("Done! Pi will be back in ~30 seconds.")
else:
    print("\nThermal protection already configured!")

ssh.close()
