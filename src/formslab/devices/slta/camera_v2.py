import os
import subprocess
from datetime import datetime

from formslab.devices.slta.camera import SLTA


class SLTAv2(SLTA):
    """
    Python wrapper that invokes slta_run_v1.sh with configurable
    EXPOSURE, NSAMP, and CLEAR parameters.

    Inherits daemon management (SilentWatch, configure, pkill) from SLTA.
    The bash scripts (slta_run_v1.sh, slta_init_v1.sh, slta_clocks_v1.sh,
    sequencer XMLs) are already deployed on the target machine in workdir.
    """

    def __init__(self,
                 interface="enxd037452af8f2",
                 target_ip="192.168.133.7",
                 host_ip="192.168.133.101",
                 workdir="/home/sensei/Soft/ltaDaemon",
                 port=8888):
        super().__init__(
            interface=interface,
            target_ip=target_ip,
            host_ip=host_ip,
            workdir=workdir,
        )
        self.port = port

    # ------------------------------------------------------------------
    # Main read orchestration — invokes slta_run_v1.sh
    # ------------------------------------------------------------------
    def read(self, cmd):
        """
        Run a full acquisition sequence by calling slta_run_v1.sh with
        EXPOSURE, NSAMP, CLEAR, RUNNAME, and PORT as CLI arguments.

        The bash script handles init, clocks, sequencer loading, params,
        optional clear frame, and image read internally.

        Uses subprocess.Popen (non-blocking) with SilentWatch monitoring
        stdout for "Image translated to fits", with a timeout to prevent
        infinite hangs on readout errors.
        """
        exposure = cmd.get("exposure", 10)
        nsamp = cmd.get("nsamp", 1)
        clear = cmd.get("clear", 30)
        mode = cmd.get("mode", "E")

        print(f"SLTAv2 read | exposure={exposure}s nsamp={nsamp} clear={clear}s mode={mode}")

        # Resolve image directory
        image_dir = self.image_dir
        if cmd.get("IMAGEDIR"):
            image_dir = os.path.join(image_dir, cmd["IMAGEDIR"])
        os.makedirs(image_dir, exist_ok=True)

        # Build runname incorporating mode and temperature
        timestamp = datetime.now().strftime("%Y%b%dT%H").upper()
        tk_part = f"K{cmd['TK']}" if cmd.get("TK") else ""
        runname = f"{timestamp}{mode}{tk_part}"

        # Construct the bash command invoking slta_run_v1.sh
        # The script accepts: --exposure, --nsamp, --clear, --runname, --port
        # IMGFOLDER is passed as an environment variable (used by slta_init_v1.sh)
        bash_cmd = (
            f"IMGFOLDER={image_dir} "
            f"./slta_run_v1.sh "
            f"--exposure {exposure} "
            f"--nsamp {nsamp} "
            f"--clear {clear} "
            f"--runname {runname} "
            f"--port {self.port}"
        )

        print(f"SLTAv2 executing: {bash_cmd}")

        read_proc = subprocess.Popen(
            f"bash -c '{bash_cmd}'",
            cwd=self.workdir,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        print(f"SLTAv2 read started | {datetime.now()}")

        # Timeout: (exposure * nsamp) + clear + 600s buffer for init, readout, FITS translation
        # Must scale with NSAMP — each sample adds ~exposure seconds to the sequence
        max_wait = (exposure * nsamp) + clear + 600

        # Monitor both configure.exe and the run script for completion
        try:
            if self.process is not None:
                self.SilentWatch(
                    [self.process, read_proc],
                    text="Image translated to fits",
                    timeout=max_wait,
                )
        except TimeoutError:
            print(f"SLTAv2 TIMEOUT after {max_wait}s — readout may have failed")
            if read_proc.poll() is None:
                read_proc.terminate()
        except RuntimeError as e:
            # _SilentWait raises RuntimeError if all subprocesses terminate
            # without printing the expected text (e.g. readout error)
            print(f"SLTAv2 read error: {e}")
            if read_proc.poll() is None:
                read_proc.terminate()

        # Wait for the run script to fully exit
        try:
            read_proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            print("SLTAv2 run script did not exit — killing")
            read_proc.kill()
            read_proc.wait()

        print(f"SLTAv2 read complete | runname={runname}")
