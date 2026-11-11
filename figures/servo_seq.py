import sys
from seqdiag import draw
P = ['MCPクライアント\n（エージェント）', 'servo-mcp\n（server.py）', 'Jetson.GPIO\n（PWM7）', 'SG90']
CL, SV, GP, SG = P
draw(P, [
    ('msg', CL, SV, 'set_servo_angle(angle_deg=30)'),
    ('note', SV, '範囲チェック（±45°）\nfault 中なら拒否'),
    ('group', 'opt: 最初の1回だけ', [
        ('msg', SV, GP, 'setup(32) / PWM(32, 50Hz)'),
    ]),
    ('msg', SV, GP, 'start / ChangeDutyCycle(9.17%)'),
    ('msg', GP, SG, '50Hz のパルス（1833µs）'),
    ('reply', SV, CL, '状態（最後に指令した角度。実測ではない）'),
    ('group', 'alt: PWM の書き込みに失敗した場合', [
        ('reply', GP, SV, '例外'),
        ('note', SV, 'fault を記録して PWM を止める'),
        ('reply', SV, CL, 'エラー（再起動まで角度の指令を拒否）'),
    ]),
], sys.argv[1], col_width=3.8)
