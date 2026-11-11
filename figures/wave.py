import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.family']=['Hiragino Sans','sans-serif']
cases=[(1000,'−45°'),(1500,'0°（中央）'),(2000,'+45°')]
fig,axes=plt.subplots(3,1,figsize=(9,5.2),sharex=True)
for ax,(w,label) in zip(axes,cases):
    t=[];v=[]
    for k in range(2):
        s=k*20
        t+= [s,s,s+w/1000,s+w/1000,s+20]; v+=[0,1,1,0,0]
    ax.plot(t,v,color='#2b6cb0',lw=2)
    ax.set_ylim(-0.25,1.75); ax.set_yticks([])
    for s in ['top','right','left']: ax.spines[s].set_visible(False)
    ax.text(-1.2,0.5,label,ha='right',va='center',fontsize=12)
    ax.annotate('',xy=(0,1.2),xytext=(w/1000,1.2),arrowprops=dict(arrowstyle='<->',color='#c05621'))
    ax.text(w/1000+0.4,1.2,f'{w}µs',ha='left',va='center',fontsize=10,color='#c05621')
axes[0].annotate('',xy=(0,1.58),xytext=(20,1.58),arrowprops=dict(arrowstyle='<->',color='#555'))
axes[0].text(10,1.62,'周期 20ms（50Hz）',ha='center',va='bottom',fontsize=10,color='#555')
axes[-1].set_xlabel('時間 [ms]')
axes[-1].set_xlim(-0.5,40.5)
fig.suptitle('SG90 に送るパルス（HIGH の幅で角度が決まる）',fontsize=13)
fig.tight_layout()
fig.savefig(sys.argv[1],dpi=150)
