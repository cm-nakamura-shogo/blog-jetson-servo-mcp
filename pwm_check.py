"""Jetson の物理 32 番ピン（PWM7）から 50Hz の PWM を出して SG90 を動かす確認用スクリプト。

PWM7 の親クロックが初期設定（pllp_out0）のままだと、pwm.start で
OSError: [Errno 22] Invalid argument になる。親クロックを clk_m に変えると出せる。
"""
import argparse
import time

import Jetson.GPIO as GPIO

PIN = 32  # BOARD 番号（PWM7）
FREQUENCY_HZ = 50
PERIOD_US = 1_000_000 / FREQUENCY_HZ


def main():
    """指定したパルス幅で PWM を出し続け、Ctrl+C で止める。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pulse-us", type=float, default=1500,
                        help="パルス幅（µs）。1000〜2000 の範囲で指定する")
    args = parser.parse_args()
    if not 1000 <= args.pulse_us <= 2000:
        parser.error("--pulse-us must be between 1000 and 2000")

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(PIN, GPIO.OUT, initial=GPIO.LOW)
    pwm = GPIO.PWM(PIN, FREQUENCY_HZ)
    try:
        pwm.start(args.pulse_us / PERIOD_US * 100)  # デューティ比（%）
        print(f"{FREQUENCY_HZ}Hz、パルス幅 {args.pulse_us:.0f}µs で出力中。Ctrl+C で止める")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pwm.stop()
        GPIO.cleanup(PIN)


if __name__ == "__main__":
    main()
