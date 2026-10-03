import os
import time
import subprocess

import psutil
import select
from datetime import datetime

class SLTA:
    def __init__(self,
                 interface="enxd037452af8f2",
                 target_ip="192.168.133.7",
                 host_ip="192.168.133.101",
                 workdir="/home/sensei/Soft/ltaDaemon"):
        self.interface = interface
        self.target_ip = target_ip
        self.host_ip = host_ip
        self.workdir = workdir
        self.image_dir = "/home/sensei/Soft/forms/data/images"
        os.makedirs(self.image_dir, exist_ok=True)
        self._last_line = ""
        self.process = None

    def SilentWatch(self, process=None, timeout=None, text=None):
        if process is None:
            timeout = 15 if timeout is None else timeout
            print("→ Waiting for AX88179 USB-Ethernet to enumerate...")
            for _ in range(timeout):
                out = subprocess.run(["ip", "link", "show"], stdout=subprocess.PIPE).stdout.decode()
                if self.interface in out:
                    print(f"✔ Interface {self.interface} is active")
                    return
                time.sleep(1)
            raise TimeoutError("✗ USB interface not detected in time")

        if text is not None:
            return self._SilentWait(process, text, timeout)

        if process.stdout is None:
            return ""
        while True:
            ready, _, _ = select.select([process.stdout], [], [], 0)
            if not ready:
                break
            line = process.stdout.readline()
            if not line:
                break
            self._last_line = line.rstrip()
            print(self._last_line)
        return self._last_line

    def _SilentWait(self, processes, text, timeout=None):
        if isinstance(processes, (list, tuple, set)):
            proc_list = []
            for p in processes:
                if isinstance(p, (list, tuple, set)):
                    proc_list.extend(p)
                else:
                    proc_list.append(p)
        else:
            proc_list = [processes]
        start = time.time()
        while True:
            for proc in proc_list[:]:
                line = self.SilentWatch(proc)
                if text in line:
                    return
                if proc.poll() is not None:
                    proc_list.remove(proc)
            if not proc_list:
                raise RuntimeError("subprocess terminated unexpectedly")
            if timeout is not None and time.time() - start > timeout:
                raise TimeoutError(f"Timed out waiting for '{text}'")
            time.sleep(0.5)

    def configure(self):
        print("🔵 Launching configure.exe...")
        cmd = (
            "bash -c '"
            "./configure.exe'"
        )
        self.process = subprocess.Popen(
            cmd,
            cwd=self.workdir,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return self.process

    def read(self,cmd):
        print(f"🔵 Running clear + image exposure for {cmd['exposure']} seconds...")
        
        timestamp = datetime.now().strftime("%Y%b%dT%H").upper()

        if cmd['IMAGEDIR'] is not None:
            self.image_dir = self.image_dir + cmd['IMAGEDIR']
        else: pass

        prefix_clear = os.path.join(self.image_dir, f"{timestamp}CR")

        if cmd['TK'] is None:
            prefix_exposure = os.path.join(self.image_dir, f"{timestamp}{cmd['mode']}{cmd['exposure']}R")
        else:
            prefix_exposure = os.path.join(self.image_dir, f"{timestamp}{cmd['mode']}{cmd['exposure']}K{cmd['TK']}R")

        script = os.path.join(self.workdir, "slta_initialization_frompython.sh")
        
        if not os.path.exists(script):
            raise RuntimeError("⚠️ Initialization script not found")

        cmd = (
            "bash -c '"
            f"source {script} && "
            f"echo === BEGIN CLEAR === && "
            f"./lta.sh 8888 name {prefix_clear} && ./lta.sh 8888 read && "
            f"echo === SLEEP START === && sleep {cmd['exposure']} && echo === SLEEP END === && "
            f"./lta.sh 8888 name {prefix_exposure} && ./lta.sh 8888 read && "
            f"echo === END SEQUENCE ==='"
        )

        read_proc = subprocess.Popen(
            cmd,
            cwd=self.workdir,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        print(f"📷 Clear and Read  → {datetime.now()}")

        if self.process is not None:
            self.SilentWatch([self.process, read_proc], text="Image translated to fits")
        read_proc.wait()

    def pkill(self):
        print("🔴 Killing configure.exe...")
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        else:
            # Was `os.system("pkill configure.exe")` -- Unix-only, and a
            # shell spawn for what psutil already does in-process.
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if (proc.info["name"] or "").lower() == "configure.exe":
                        proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
