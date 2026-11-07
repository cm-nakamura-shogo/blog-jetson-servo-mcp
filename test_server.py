import asyncio
import math
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from mcp import Client

from server import Servo, make_server


def test_http_auth_host_and_mcp(tmp_path):
    import json
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    from urllib.error import HTTPError, URLError
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = "test-only-token-" + "x" * 32
    log_file = (tmp_path / "server.log").open("w")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("server.py")), "--simulate", "--port", str(port),
         "--lock-file", str(tmp_path / "server.lock")],
        env={**os.environ, "SERVO_MCP_TOKEN": token},
        stdout=subprocess.DEVNULL, stderr=log_file,
    )

    def request(method, params=None, auth=True, host=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-11-25"}
        if auth:
            headers["Authorization"] = "Bearer " + token
        if host:
            headers["Host"] = host
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           "params": params or {}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    try:
        for _ in range(50):
            try:
                request("tools/list", auth=False)
            except HTTPError as error:
                assert error.code == 401
                break
            except URLError:
                time.sleep(0.1)
        else:
            pytest.fail("HTTP server did not start")
        with pytest.raises(HTTPError) as error:
            request("tools/list", host="unexpected.example")
        assert error.value.code == 421
        result = request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}})
        assert "result" in result
        result = request("tools/list")
        assert {tool["name"] for tool in result["result"]["tools"]} == {
            "set_servo_angle", "get_servo_status", "stop_servo"}
        result = request("tools/call", {"name": "set_servo_angle", "arguments": {"angle_deg": 0}})
        assert not result["result"].get("isError", False)
        result = request("tools/call", {"name": "stop_servo", "arguments": {}})
        assert not result["result"].get("isError", False)
    finally:
        process.terminate()
        process.wait(timeout=10)
        log_file.close()
    logs = (tmp_path / "server.log").read_text()
    assert token not in logs
    events = [json.loads(line) for line in logs.splitlines() if line.startswith('{"timestamp"')]
    pwm = next(e for e in events if e["event"] == "pwm_write_finished")
    assert pwm["pulse_us"] == 1500
    assert pwm["request_id"] is not None
    related = [e for e in events if e["request_id"] == pwm["request_id"]]
    assert {e["event"] for e in related} >= {
        "http_received", "tool_started", "lock_acquired", "pwm_write_started",
        "pwm_write_finished", "tool_finished", "http_finished"}
    assert next(e for e in related if e["event"] == "http_finished")["status"] == 200


def test_pwm_timing_and_failure_logs(caplog):
    import json
    import logging
    import time
    caplog.set_level(logging.INFO, logger="servo_mcp")
    gpio = Mock()
    gpio.PWM.return_value.start.side_effect = lambda duty: time.sleep(0.02)
    servo = Servo()
    with patch("server.importlib.import_module", return_value=gpio):
        servo.set_angle(0)
        gpio.PWM.return_value.ChangeDutyCycle.side_effect = OSError("private details")
        with pytest.raises(OSError):
            servo.set_angle(10)
    events = [json.loads(record.message) for record in caplog.records]
    complete = next(e for e in events if e["event"] == "pwm_write_finished")
    assert complete["elapsed_ms"] >= 20
    failed = next(e for e in events if e["event"] == "pwm_write_failed")
    assert failed["error_type"] == "OSError"
    assert "private details" not in caplog.text


@pytest.mark.parametrize("angle", [-46, 46, math.nan, math.inf, True])
def test_invalid_angle_never_initializes_gpio(angle):
    servo = Servo()
    with patch("server.importlib.import_module") as load:
        with pytest.raises(ValueError):
            servo.set_angle(angle)
        load.assert_not_called()


def test_pwm_lifecycle_and_calibration():
    gpio = Mock()
    servo = Servo()
    assert not servo.status()["pwm_enabled"]
    with patch("server.importlib.import_module", return_value=gpio):
        servo.set_angle(0)
        gpio.PWM.assert_called_once_with(32, 50)
        gpio.PWM.return_value.start.assert_called_once_with(7.5)
        servo.set_angle(-45)
        gpio.PWM.return_value.ChangeDutyCycle.assert_called_with(5.0)
        assert servo.status()["commanded_pulse_us"] == 1000
        servo.set_angle(45)
        gpio.PWM.return_value.ChangeDutyCycle.assert_called_with(10.0)
        assert servo.status()["commanded_pulse_us"] == 2000
        servo.stop()
        assert not servo.status()["pwm_enabled"]
        servo.set_angle(0)
        assert gpio.PWM.call_count == 1
        assert servo.status()["commanded_pulse_us"] == 1500
        servo.close()
        gpio.cleanup.assert_called_once_with(32)


def test_pwm_failure_latches_fault():
    gpio = Mock()
    gpio.PWM.return_value.start.side_effect = OSError("Invalid argument")
    servo = Servo()
    with patch("server.importlib.import_module", return_value=gpio):
        with pytest.raises(OSError):
            servo.set_angle(0)
        assert servo.status()["fault"]
        with pytest.raises(RuntimeError):
            servo.set_angle(0)


def test_mcp_tools_simulated():
    async def check():
        servo = Servo(simulate=True)
        async with Client(make_server(servo)) as client:
            result = await client.call_tool("get_servo_status", {})
            assert not result.is_error
            assert not servo.enabled
            result = await client.call_tool("set_servo_angle", {"angle_deg": 0})
            assert not result.is_error
            assert servo.angle == 0
            result = await client.call_tool("set_servo_angle", {"angle_deg": 180})
            assert result.is_error
            assert servo.angle == 0
            result = await client.call_tool("stop_servo", {})
            assert not result.is_error
            assert not servo.enabled
    asyncio.run(check())


def test_tool_schema_snapshot():
    import json

    async def check():
        async with Client(make_server(Servo(simulate=True))) as client:
            result = await client.list_tools()
            tools = result.tools if hasattr(result, "tools") else result
            actual = {"tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools]}
        snapshot = Path(__file__).resolve().parent / "schemas" / "servo-mcp-tools.json"
        assert actual == json.loads(snapshot.read_text())

    asyncio.run(check())
