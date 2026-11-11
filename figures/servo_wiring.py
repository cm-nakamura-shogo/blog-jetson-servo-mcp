import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
plt.rcParams['font.family'] = ['Hiragino Sans', 'sans-serif']
out = sys.argv[1]
fig, ax = plt.subplots(figsize=(11, 5.4))
ax.set_xlim(0, 110); ax.set_ylim(4, 55); ax.axis('off')

def box(x, y, w, h, title, sub=None, fc='#edf2f7', ec='#4a5568'):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.3,rounding_size=1.2', fc=fc, ec=ec, lw=1.6))
    ax.text(x + w / 2, y + h - 2.6, title, ha='center', va='center', fontsize=11.5, fontweight='bold', color='#1a202c')
    if sub:
        ax.text(x + w / 2, y + h - 5.6, sub, ha='center', va='center', fontsize=8.5, color='#4a5568')

def term(x, y, label, ha='left'):
    ax.plot(x, y, 'o', ms=6, color='#2d3748', zorder=5)
    dx = 1.2 if ha == 'left' else -1.2
    ax.text(x + dx, y, label, ha=ha, va='center', fontsize=9, color='#1a202c')

def wire(pts, color, lw=2.6):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, lw=lw, solid_capstyle='round', zorder=3)

RED, BLK, BRN, YEL, BLU = '#e53e3e', '#2d3748', '#8b5a2b', '#d69e2e', '#3182ce'

# 機器（写真と同じ並び）
box(2, 20, 17, 26, 'SG90', 'サーボ')
box(28, 20, 19, 26, 'サーボテスター', '電源線の中継のみ')
box(56, 20, 20, 26, '外部電源', 'OMRON 5V')
box(85, 20, 22, 26, 'Jetson', 'Orin Nano Super')

# SG90 の線
term(19, 37, '赤（+）', ha='right'); term(19, 32, '茶（−）', ha='right'); term(19, 27, '黄（信号）', ha='right')
# テスター
term(28, 37, 'OUT+'); term(28, 32, 'OUT−')
term(47, 37, 'IN+', ha='right'); term(47, 32, 'IN−', ha='right')
ax.text(37.5, 24.2, 'S端子は使わない', ha='center', fontsize=8.5, color='#718096')
# 電源
term(56, 37, 'DC+'); term(56, 32, 'DC−')
# Jetson 40ピンヘッダー
term(85, 32, '34番（GND）'); term(85, 27, '32番（PWM7）')
ax.text(96, 23, '5Vピンは使わない', ha='center', fontsize=8.5, color='#718096')

# 電源系: 電源 → テスター → SG90
wire([(56, 37), (47, 37)], RED); wire([(28, 37), (19, 37)], RED)
wire([(56, 32), (47, 32)], BLK); wire([(28, 32), (19, 32)], BRN)
# GND の共通化: Jetson 34番 → 電源 DC−
wire([(85, 32), (80, 32), (80, 14), (52, 14), (52, 32), (56, 32)], BLK)
ax.text(66, 15.6, 'GNDの共通化（34番 → DC−）', ha='center', fontsize=9.5, color='#1a202c')
# 信号: Jetson 32番 → SG90 黄
wire([(85, 27), (82, 27), (82, 8), (23, 8), (23, 27), (19, 27)], BLU)
ax.text(52, 9.6, '信号（Jetson 32番 → SG90 黄、50HzのPWM）', ha='center', fontsize=9.5, color='#1a202c')
# 凡例
ax.text(2, 52, '電源（5V）: 外部電源 → サーボテスター → SG90 ／ Jetsonからは信号とGNDだけをつなぐ', fontsize=10, color='#2d3748')
fig.savefig(out, dpi=150, bbox_inches='tight', facecolor='white')
