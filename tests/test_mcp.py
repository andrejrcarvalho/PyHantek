"""End-to-end test: start the app with the demo device and drive it over MCP."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from pyhantek.processing import measure


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, url):
        self.url = url
        self.id = 0

    def rpc(self, method, params=None):
        self.id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.id, "method": method, "params": params or {}}).encode()
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        if res.get("isError"):
            return {"_error": res["content"][0]["text"]}
        item = res["content"][0]
        return item if item["type"] != "text" else json.loads(item["text"])


@pytest.fixture(scope="module")
def client():
    port = _free_port()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYHANTEK_PROFILE="pytest")
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyhantek", "--demo", "--mcp-port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    c = Client(f"http://127.0.0.1:{port}/mcp")
    deadline = time.time() + 30
    while True:
        try:
            c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}})
            break
        except (urllib.error.URLError, ConnectionError):
            if proc.poll() is not None or time.time() > deadline:
                proc.kill()
                pytest.fail("app did not start: " + (proc.stderr.read() or "")[-2000:])
            time.sleep(0.2)
    # known starting point
    c.call("set_trigger", mode="Auto", source="CH1", level_volts=0.0, position=0.5)
    c.call("set_math", enabled=False)
    c.call("set_channel", channel="CH1", enabled=True, volts_per_div=1, probe=1, position_div=2)
    c.call("set_channel", channel="CH2", enabled=True, volts_per_div=1, probe=1, position_div=-2)
    c.call("set_horizontal", seconds_per_div=500e-6, samplerate=0, calfreq=1000)
    c.call("run")
    yield c
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_tools_listed(client):
    names = {t["name"] for t in client.rpc("tools/list")["result"]["tools"]}
    assert {"get_status", "set_channel", "set_horizontal", "set_trigger", "get_measurements", "get_waveform",
            "get_spectrum", "screenshot", "autoset", "single"} <= names


def test_status_and_snapping(client):
    st = client.call("get_status")
    assert st["demo"] is True and st["running"] is True
    r = client.call("set_channel", channel="CH1", volts_per_div=0.7)
    assert r["vdiv"] == 0.5
    client.call("set_channel", channel="CH1", volts_per_div=1)


def test_measurements_follow_settings(client):
    client.call("set_horizontal", calfreq=2000)
    m = client.call("get_measurements")
    ch1, ch2 = m["channels"]["CH1"], m["channels"]["CH2"]
    assert ch1["freq"] == pytest.approx(1000, rel=0.02)
    assert ch2["freq"] == pytest.approx(2000, rel=0.02)  # calibration output followed the new setting
    assert ch2["duty"] == pytest.approx(0.5, abs=0.03)
    client.call("set_horizontal", calfreq=1000)


def test_waveform_and_spectrum(client):
    w = client.call("get_waveform", channel="CH2", max_points=200)
    assert len(w["time_s"]) == 200 == len(w["volts"])
    assert min(w["volts"]) == pytest.approx(0, abs=0.1) and max(w["volts"]) == pytest.approx(2, abs=0.1)
    m = measure(np.array(w["volts"]), w["samplerate_effective"])
    assert m.freq == pytest.approx(1000, rel=0.05)
    client.call("set_horizontal", seconds_per_div=10e-3)  # many periods -> THD is meaningful
    sp = client.call("get_spectrum", channel="CH1", peaks=3)
    assert sp["peaks"][0]["freq_hz"] == pytest.approx(1000, rel=0.01)
    assert sp["thd_percent"] == pytest.approx(16.7, abs=1.5)  # demo: 0.25 V 3rd harmonic on 1.5 V
    client.call("set_horizontal", seconds_per_div=500e-6)


def test_single_timeout_and_validation(client):
    client.call("set_trigger", level_volts=50)
    r = client.call("single", timeout_s=1.5)
    assert "error" in r and "hint" in r
    client.call("set_trigger", mode="Auto", level_volts=0)
    client.call("run")
    bad = client.call("set_channel", channel="CH2", coupling="XX")
    assert "_error" in bad


def test_autoset_and_screenshot(client):
    r = client.call("autoset")
    assert "error" not in r
    assert r["measurements"]["channels"]["CH1"]["freq"] == pytest.approx(1000, rel=0.02)
    img = client.call("screenshot")
    assert img["type"] == "image" and img["mimeType"] == "image/png"
