"""Jetson Orin Nano につないだ SG90 を MCP で操作する。

起動しただけでは PWM を出さない。systemd の再起動や OS の起動のたびに、
人が見ていないところでサーボが動くのを避けるため。
"""
import argparse
import contextlib
import fcntl
import importlib
import json
import logging
import math
import os
import secrets
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import PlainTextResponse


# 物理 32 番（PWM7）は、50Hz を出せるよう親クロックを clk_m に変えてある唯一のピン。
PIN = 32  # BOARD 番号
FREQUENCY_HZ = 50
PERIOD_US = 1_000_000 / FREQUENCY_HZ
CENTER_PULSE_US = 1500

# 実機の目測では 1000〜2000µs（約 90°）がほぼ最大の可動範囲なので、±45° で打ち切る。
PULSE_US_PER_90_DEG = 1000
ANGLE_LIMIT_DEG = 45

logger = logging.getLogger("servo_mcp")
request_id = ContextVar("servo_request_id", default=None)


# --- ログ ---------------------------------------------------------------

def event(name, **fields):
    """1 件のイベントを 1 行の JSON でログに出す。HTTP の要求の中なら request_id を付ける。"""
    # Authorization ヘッダー、リクエスト本文、例外メッセージはログに出さない。
    # journalctl は Jetson にログインできる人なら読めるので、トークンや内部の詳細を残さない。
    logger.info(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                            "event": name, "request_id": request_id.get(), **fields}))


def elapsed_ms(started):
    """perf_counter() で取った開始時刻からの経過時間をミリ秒で返す。"""
    return (time.perf_counter() - started) * 1000


@contextlib.contextmanager
def timed(name, **fields):
    """with の中の処理を {name}_started / _finished / _failed のイベントで挟み、所要時間を記録する。"""
    started = time.perf_counter()
    event(name + "_started", **fields)
    try:
        yield
    except Exception as error:
        event(name + "_failed", elapsed_ms=elapsed_ms(started),
              error_type=type(error).__name__, **fields)
        raise
    event(name + "_finished", elapsed_ms=elapsed_ms(started), **fields)


def configure_logging():
    """servo_mcp ロガーを、メッセージだけを stderr に出す設定にする。"""
    handler = logging.StreamHandler()  # stderr に出すので stdio の MCP を壊さない。
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


# --- サーボ -----------------------------------------------------------------

def pulse_us(angle):
    """角度（°）をパルス幅（µs）に換算する。0° が 1500µs の線形近似。"""
    return CENTER_PULSE_US + angle * PULSE_US_PER_90_DEG / 90


class Servo:
    """SG90 1 台の PWM 出力と、最後に指令した角度を持つ。

    simulate=True のときは GPIO に触らず、状態だけを更新する。
    状態の読み書きは self.lock で直列化し、MCP の要求が同時に来ても 1 件ずつ処理する。
    """
    def __init__(self, simulate=False):
        self.simulate = simulate
        # set_angle が失敗したときにロックを持ったまま stop を呼ぶので、再入できるロックにする。
        self.lock = threading.RLock()
        self.gpio = None
        self.pwm = None
        self.enabled = False
        self.angle = None
        self.fault = None

    @contextlib.contextmanager
    def measured_lock(self, operation):
        """ロックを取り、取れるまでに待った時間を lock_acquired としてログに出す。"""
        started = time.perf_counter()
        with self.lock:
            event("lock_acquired", operation=operation, wait_ms=elapsed_ms(started))
            yield

    def status(self):
        """現在の状態を辞書で返す。値は指令値で、実際の角度を測ったものではない。"""
        with self.lock:
            return {
                "simulation": self.simulate,
                "pwm_enabled": self.enabled,
                "commanded_angle_deg": self.angle,
                "commanded_pulse_us": None if self.angle is None else pulse_us(self.angle),
                "physical_pin": PIN,
                "frequency_hz": FREQUENCY_HZ,
                "fault": self.fault,
                "note": "Commanded position only; no angle or power feedback. Calibration is approximate.",
            }

    def set_angle(self, angle):
        """角度を検証してから PWM に書き込み、状態を返す。

        範囲外の値は GPIO に触る前に ValueError で弾く。書き込みに失敗したら fault を記録して
        PWM を止め、元の例外を投げ直す。fault が残っている間は RuntimeError で拒む。
        """
        # bool は int のサブクラスなので、弾かないと True が 1° として通ってしまう。
        if (isinstance(angle, bool) or not math.isfinite(angle)
                or not -ANGLE_LIMIT_DEG <= angle <= ANGLE_LIMIT_DEG):
            raise ValueError(f"angle_deg must be finite and between "
                             f"-{ANGLE_LIMIT_DEG} and {ANGLE_LIMIT_DEG}")
        with self.measured_lock("set_angle"):
            # PWM の操作に一度失敗したら、配線や電源を人が確かめるまで動かさない。
            # 自動で再開すると、原因が残ったまま動かし続けることになる。
            if self.fault:
                raise RuntimeError("Servo fault; inspect hardware and restart the server")
            pulse = pulse_us(angle)
            try:
                with timed("pwm_write", angle_deg=angle, pulse_us=pulse, simulation=self.simulate,
                           operation="change" if self.enabled else "start"):
                    if not self.simulate:
                        self._write_pwm(pulse / PERIOD_US * 100)
                self.angle = angle
                self.enabled = True
            except Exception as error:
                self.fault = str(error)
                # 止められるなら止めておく。ここでの失敗は握りつぶし、元の例外を呼び出し元に返す。
                with contextlib.suppress(Exception):
                    self.stop()
                raise
            return self.status()

    def _write_pwm(self, duty):
        """デューティ比（%）を PWM に書き込む。最初の 1 回は GPIO と PWM を初期化して start する。"""
        # 起動時にピンを駆動しないよう、GPIO は最初の書き込みで初期化する。
        # import もここで行うので、Jetson.GPIO のない Mac でもテストできる。
        if self.gpio is None:
            self.gpio = importlib.import_module("Jetson.GPIO")
            self.gpio.setmode(self.gpio.BOARD)
            self.gpio.setup(PIN, self.gpio.OUT, initial=self.gpio.LOW)
        if self.pwm is None:
            self.pwm = self.gpio.PWM(PIN, FREQUENCY_HZ)
        if self.enabled:
            self.pwm.ChangeDutyCycle(duty)
        else:
            self.pwm.start(duty)

    def stop(self):
        """PWM を止めて状態を返す。指令した角度は消さない。止められなければ fault を記録して例外を投げる。"""
        with self.lock:
            if self.pwm is not None:
                try:
                    self.pwm.stop()
                except Exception as error:
                    self.fault = f"PWM stop failed: {error}"
                    raise
            self.enabled = False
            return self.status()

    def close(self):
        """PWM を止め、ピンを解放する。サーバーの終了時に呼ぶ。"""
        try:
            self.stop()
        finally:
            if self.gpio is not None:
                self.gpio.cleanup(PIN)


# --- MCP ツール -------------------------------------------------------------

WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
READ = ToolAnnotations(readOnlyHint=True)

# エージェントに渡る tool の説明。schemas/servo-mcp-tools.json と一致させる。
# LLM が読む文なので英語で書き、docstring とは分けておく。
SET_SERVO_ANGLE_DESCRIPTION = (
    "Move the physical SG90 to an approximate angle (-45..+45 degrees, center = 0) and hold it.\n"
    "\n"
    "External 5V supply must be on and grounds connected. This physically moves\n"
    "hardware. -45/0/+45 correspond to 1000/1500/2000us. No measured-angle feedback.\n"
    "PWM keeps running after this call and after the client disconnects."
)
GET_SERVO_STATUS_DESCRIPTION = (
    "Get last commanded position and software PWM state; does not move hardware."
)
STOP_SERVO_DESCRIPTION = (
    "Stop PWM. Does NOT switch off the external supply or guarantee mechanical braking."
)


def make_server(servo):
    """servo を操作する 3 つのツールを登録した MCP サーバーを作る。"""
    # 正常に終了するときは PWM を止め、ピンを解放する。
    @contextlib.asynccontextmanager
    async def lifespan(_):
        try:
            yield {}
        finally:
            servo.close()

    mcp = MCPServer("orin-nano-1-servo", lifespan=lifespan)

    @mcp.tool(description=SET_SERVO_ANGLE_DESCRIPTION, annotations=WRITE)
    def set_servo_angle(angle_deg: float) -> dict:
        """サーボを angle_deg に動かし、その角度を保持する。"""
        with timed("tool", tool="set_servo_angle", angle_deg=angle_deg):
            return servo.set_angle(angle_deg)

    @mcp.tool(description=GET_SERVO_STATUS_DESCRIPTION, annotations=READ)
    def get_servo_status() -> dict:
        """現在の状態を返す。サーボは動かさない。"""
        with timed("tool", tool="get_servo_status"):
            return servo.status()

    @mcp.tool(description=STOP_SERVO_DESCRIPTION, annotations=WRITE)
    def stop_servo() -> dict:
        """PWM を止める。"""
        with timed("tool", tool="stop_servo"):
            return servo.stop()

    return mcp


# --- HTTP ミドルウェア -------------------------------------------------------

class RequestTiming:
    """HTTP の要求ごとに request_id を振り、受付から応答までの時間をログに出す。

    サーボの動きが遅れたときに、ネットワーク、ロック待ち、GPIO のどこで時間がかかったかを切り分けるため。
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = request_id.set(uuid.uuid4().hex)
        started = time.perf_counter()
        status = None

        async def measured_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            event("http_received", peer=(scope.get("client") or [None])[0])
            await self.app(scope, receive, measured_send)
        except Exception as error:
            event("http_failed", error_type=type(error).__name__)
            raise
        finally:
            event("http_finished", status=status, elapsed_ms=elapsed_ms(started))
            request_id.reset(token)


class BearerGate:
    """VPN 内のクライアント向けの事前共有 Bearer トークン。OAuth のディスカバリーではない。"""
    def __init__(self, app, token):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            # 応答時間の差からトークンを推測されないよう、一定時間で比較する。
            if not secrets.compare_digest(headers.get(b"authorization", b""), self.expected):
                response = PlainTextResponse("Unauthorized", status_code=401,
                                             headers={"WWW-Authenticate": "Bearer"})
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


def serve_http(mcp, host, port, token):
    """MCP サーバーを Streamable HTTP で公開し、Bearer トークンで認証する。終了するまで戻らない。"""
    # Host と Origin を絞り、DNS リバインディングでブラウザから叩かれるのを防ぐ。
    # Jetson の上から curl で確かめられるよう、127.0.0.1 と localhost も許す。
    hosts = list(dict.fromkeys([f"{host}:{port}", f"127.0.0.1:{port}", f"localhost:{port}"]))
    security = TransportSecuritySettings(
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts],
    )
    # ステートレスにして、サービスを再起動してもクライアントがセッションを張り直さずに済むようにする。
    # json_response にすると SSE を解釈しなくてよいので、curl でそのまま読める。
    app = mcp.streamable_http_app(stateless_http=True, json_response=True,
                                  transport_security=security)
    # RequestTiming を外側に置くのは、401 で弾いた要求もログに残すため。
    # サーボの状態と GPIO は 1 つのプロセスにしか持てないので、worker は 1 つにする。
    uvicorn.run(RequestTiming(BearerGate(app, token)), host=host, port=port, workers=1)


# --- 起動処理 -----------------------------------------------------------

def parse_args():
    """コマンドライン引数を読む。HTTP で起動するのに SERVO_MCP_TOKEN が短すぎれば、ここで終了する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lock-file", default="/tmp/orin-nano-1-servo-mcp.lock")
    args = parser.parse_args()
    args.token = os.environ.get("SERVO_MCP_TOKEN", "")
    if args.transport == "http" and len(args.token) < 32:
        parser.error("Set SERVO_MCP_TOKEN to a random token of at least 32 characters")
    return args


def main():
    """ロックファイルで二重起動を防いでから、指定された transport でサーバーを起動する。"""
    configure_logging()
    args = parse_args()

    # このサーバーの二重起動を防ぐ。2 つのプロセスが同じピンに PWM を書くと、どちらの角度になるか分からない。
    # 旧来の単体スクリプトはこのロックを見ないので、競合は防げない。
    # 既定の置き場所が /tmp なので、O_NOFOLLOW でシンボリックリンクの差し替えを拒む。
    # LOCK_NB で、既に動いていればロックを待たずにすぐ失敗させる。
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open(args.lock_file, flags, 0o600), "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mcp = make_server(Servo(args.simulate))
        if args.transport == "stdio":
            mcp.run()
        else:
            serve_http(mcp, args.host, args.port, args.token)


if __name__ == "__main__":
    main()
