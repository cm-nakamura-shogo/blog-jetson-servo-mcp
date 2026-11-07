# blog-jetson-servo-mcp

Jetson Orin Nano Super の 40 ピンヘッダー（物理 32 番、PWM7）につないだ SG90 サーボを、MCP サーバーとして公開するサンプルです。

ブログ記事: <!-- TODO: 記事のURL -->

## 構成

| ファイル | 内容 |
| --- | --- |
| `server.py` | MCP サーバー本体（Streamable HTTP、Bearer 認証） |
| `test_server.py` | テスト（`--simulate` 相当で GPIO に触らずに動く） |
| `schemas/servo-mcp-tools.json` | 公開しているツール定義のスナップショット |
| `deploy/servo-mcp.service` | systemd のユニットファイル |
| `config/` | MCP クライアントの設定例 |

## ツール

| ツール | 動作 |
| --- | --- |
| `set_servo_angle(angle_deg)` | −45〜+45° の近似角度を指令して保持する。サーボが物理的に動く |
| `get_servo_status()` | 最後の指令値と PWM の状態を返す。サーボは動かない |
| `stop_servo()` | PWM を止める。外部電源は切れず、機械的な制動もかからない |

## 前提

- PWM7 の親クロックを `clk_m`（19.2MHz）に変更してあること。初期設定の `pllp_out0` のままでは 50Hz を出せない
- サーボの電源は外部の 5V 電源から取り、Jetson の GND と共通にすること

## セットアップ

Jetson 上で実行します。

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
(umask 077; test -e .token || .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))' > .token)
export SERVO_MCP_TOKEN="$(cat .token)"

# シミュレーション（GPIO に触らない）
.venv/bin/python server.py --simulate --host <JETSON_IP>

# 実機
sudo --preserve-env=SERVO_MCP_TOKEN .venv/bin/python server.py --host <JETSON_IP> --lock-file /run/orin-nano-1-servo-mcp.lock
```

## テスト

Jetson.GPIO は実機を動かすときにしか import しないので、Mac でもテストできます。

```bash
uv run --no-project --with-requirements requirements.txt --with pytest python -m pytest -q
```

## 注意

- 実際の角度は測っていません。状態として返すのは最後に指令した値です
- PWM はクライアントが切断しても出たままです。強制終了や OS の異常時に止まる保証はないので、物理的に止めるときは外部電源を切ってください
- 認証は固定の Bearer トークンです。信頼できる LAN / VPN の中だけで使ってください
