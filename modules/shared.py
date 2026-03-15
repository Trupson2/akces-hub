"""
Współdzielone zasoby — CSS i inne stałe używane przez wiele modułów.
Import: from modules.shared import CSS
(Unika circular import z app.py)
"""

CSS = '''
<style>
:root {
    --bg-primary: #0a0a0f;
    --bg-secondary: #12121a;
    --bg-tertiary: #1e1e2e;
    --border-color: #2a2a3a;
    --text-primary: #ffffff;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent-blue: #3b82f6;
    --accent-green: #22c55e;
    --accent-yellow: #eab308;
    --accent-red: #ef4444;
    --accent-purple: #8b5cf6;
    --accent-orange: #ff5a00;
    --nav-bg: #0a0a0f;
}
[data-theme="light"] {
    --bg-primary: #f8fafc; --bg-secondary: #ffffff; --bg-tertiary: #f1f5f9;
    --border-color: #e2e8f0; --text-primary: #1e293b; --text-secondary: #475569;
    --text-muted: #94a3b8; --accent-blue: #2563eb; --accent-green: #16a34a;
    --accent-yellow: #ca8a04; --accent-red: #dc2626; --accent-purple: #7c3aed;
    --accent-orange: #ea580c; --nav-bg: #ffffff;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg-primary);color:var(--text-primary);min-height:100vh;transition:background 0.3s,color 0.3s}
button,a,.btn,.card,.quick-btn,.module,.tool-card,.list-item,[onclick]{-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;outline:none}
button:active,a:active,.btn:active,[onclick]:active{outline:none}
body.kiosk,body.kiosk *{cursor:none!important}
.container{max-width:1600px;margin:0 auto;padding:20px;padding-bottom:90px}
.header{text-align:center;padding:25px 0;border-bottom:1px solid var(--border-color);margin-bottom:25px}
.header h1{font-size:1.8rem;background:linear-gradient(135deg,var(--accent-blue),var(--accent-purple));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.header small{color:var(--text-muted);font-size:0.85rem}
.theme-toggle{position:fixed;top:15px;right:15px;z-index:200;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:50%;width:44px;height:44px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1.3rem;transition:all 0.3s}
.theme-toggle:hover{transform:scale(1.1);border-color:var(--accent-blue)}
.card{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:16px;padding:18px;margin-bottom:15px;transition:all 0.2s}
.card:hover{border-color:var(--accent-blue)}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.card-title{font-weight:600;font-size:1.05rem}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.stat{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:16px;text-align:center;transition:all 0.2s}
.stat:hover{border-color:var(--accent-blue)}
.stat-value{font-size:1.6rem;font-weight:700;color:var(--accent-blue)}
.stat-value.green{color:var(--accent-green)}
.stat-value.yellow{color:var(--accent-yellow)}
.stat-label{font-size:0.8rem;color:var(--text-muted);text-transform:uppercase;margin-top:5px}
.today-stats{background:linear-gradient(135deg,rgba(34,197,94,0.1),rgba(16,185,129,0.1));border:1px solid rgba(34,197,94,0.3);border-radius:16px;padding:20px;margin-bottom:20px}
.today-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}
.today-title{color:var(--accent-green);font-weight:600;font-size:1.15rem}
.today-date{color:var(--text-muted);font-size:0.85rem}
.today-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;text-align:center}
.today-value{font-size:2rem;font-weight:700;color:var(--accent-green)}
.today-label{font-size:0.8rem;color:var(--text-muted)}
.quick-actions{display:grid;grid-template-columns:repeat(6,1fr);gap:15px;margin-bottom:20px}
.quick-btn{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:20px 15px;text-align:center;color:var(--text-primary);text-decoration:none;transition:all 0.2s}
.quick-btn:hover{border-color:var(--accent-blue);transform:translateY(-2px)}
.quick-btn .icon{font-size:1.8rem;margin-bottom:10px}
.quick-btn .label{font-size:0.85rem;color:var(--text-secondary)}
.quick-btn.active{border-color:var(--accent-green);background:rgba(34,197,94,0.1)}
.quick-btn.alert{border-color:var(--accent-red);background:rgba(239,68,68,0.1)}
.modules-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:15px;margin-bottom:20px}
.module{background:linear-gradient(135deg,var(--bg-tertiary),var(--bg-secondary));border:1px solid var(--border-color);border-radius:16px;padding:20px;margin-bottom:0;text-decoration:none;color:var(--text-primary);display:block;transition:all 0.2s}
.module:hover{border-color:var(--accent-blue);transform:translateY(-3px)}
.module.purple{background:linear-gradient(135deg,rgba(139,92,246,0.2),rgba(88,28,135,0.2));border-color:rgba(139,92,246,0.3)}
.module.blue{background:linear-gradient(135deg,rgba(59,130,246,0.2),rgba(37,99,235,0.2));border-color:rgba(59,130,246,0.3)}
.module.orange{background:linear-gradient(135deg,rgba(255,90,0,0.2),rgba(200,70,0,0.2));border-color:rgba(255,90,0,0.3)}
.module-header{display:flex;align-items:center;gap:14px;margin-bottom:12px}
.module-icon{font-size:2.4rem}
.module-title{font-weight:700;font-size:1.2rem}
.module-desc{font-size:0.9rem;color:var(--text-secondary)}
.module-stats{display:flex;gap:12px;margin-top:14px;flex-wrap:wrap}
.module-stat{background:rgba(0,0,0,0.2);padding:8px 14px;border-radius:8px;font-size:0.85rem}
.module-stat strong{color:var(--accent-green)}
.btn{display:block;width:100%;padding:15px;font-size:1rem;font-weight:600;text-align:center;text-decoration:none;border:none;border-radius:12px;cursor:pointer;margin-bottom:12px;color:#fff;transition:all 0.2s}
.btn-primary{background:var(--accent-blue)}.btn-primary:hover{background:#2563eb;transform:translateY(-1px)}
.btn-success{background:var(--accent-green)}.btn-success:hover{background:#16a34a}
.btn-purple{background:linear-gradient(135deg,var(--accent-purple),#7c3aed)}
.btn-secondary{background:var(--bg-tertiary);border:1px solid var(--border-color);color:var(--text-primary)}
.btn-danger{background:var(--accent-red)}.btn-warning{background:var(--accent-yellow);color:#000}
.btn-sm{padding:10px 18px;font-size:0.9rem;width:auto;display:inline-block}
.tools-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px}
.tool-card{background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:14px;padding:18px;text-decoration:none;color:var(--text-primary);transition:all 0.2s}
.tool-card:hover{border-color:var(--accent-blue);transform:translateY(-2px)}
.tool-icon{font-size:2rem;margin-bottom:10px}.tool-name{font-weight:600;font-size:0.95rem}
.tool-desc{font-size:0.75rem;color:var(--text-muted);margin-top:5px}
.list-item{display:flex;align-items:center;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:14px;margin-bottom:10px;text-decoration:none;color:var(--text-primary);transition:all 0.2s}
.list-item:hover{border-color:var(--accent-blue)}
.list-item img{width:52px;height:52px;object-fit:contain;background:#fff;border-radius:10px;margin-right:14px}
.list-item-info{flex:1;min-width:0}.list-item-title{font-weight:600;font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-item-meta{font-size:0.8rem;color:var(--text-muted)}.list-item-right{text-align:right;margin-left:12px}
.list-item-value{font-weight:700;color:var(--accent-blue)}.list-item-sub{font-size:0.75rem;color:var(--text-muted)}
.activity-item{display:flex;align-items:center;gap:14px;padding:14px;background:var(--bg-secondary);border-radius:12px;margin-bottom:10px}
.activity-dot{width:10px;height:10px;border-radius:50%}
.activity-dot.green{background:var(--accent-green)}.activity-dot.yellow{background:var(--accent-yellow)}.activity-dot.red{background:var(--accent-red)}
.activity-content{flex:1}.activity-msg{font-size:0.95rem}.activity-time{font-size:0.75rem;color:var(--text-muted)}
.form-group{margin-bottom:18px}.form-group label{display:block;font-size:0.85rem;color:var(--text-secondary);margin-bottom:8px;font-weight:500}
.form-control{width:100%;padding:14px;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:10px;color:var(--text-primary);font-size:1rem;transition:border-color 0.2s}
.form-control:focus{outline:none;border-color:var(--accent-blue)}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.alert{padding:14px 18px;border-radius:12px;margin-bottom:18px;font-size:0.95rem}
.alert-success{background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.3);color:var(--accent-green)}
.alert-warning{background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.3);color:var(--accent-yellow)}
.alert-error{background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:var(--accent-red)}
.status-bar{display:flex;align-items:center;justify-content:space-between;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:12px;padding:14px 18px;margin-bottom:18px}
.status-bar.online{border-color:rgba(34,197,94,0.5);background:rgba(34,197,94,0.1)}
.status-bar.offline{border-color:rgba(239,68,68,0.5);background:rgba(239,68,68,0.1)}
.status-indicator{display:flex;align-items:center;gap:12px}
.status-dot{width:12px;height:12px;border-radius:50%;background:var(--text-muted)}
.status-dot.online{background:var(--accent-green);animation:pulse 2s infinite}
.status-dot.offline{background:var(--accent-red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
.section-title{color:var(--accent-blue);font-weight:600;font-size:0.95rem;margin:25px 0 15px;display:flex;align-items:center;gap:10px}
.calc-result{background:var(--bg-primary);border-radius:12px;padding:18px;margin-top:18px}
.calc-row{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border-color)}
.calc-row:last-child{border:none}.calc-label{color:var(--text-secondary)}.calc-value{font-weight:700}
.calc-value.green{color:var(--accent-green)}.calc-value.red{color:var(--accent-red)}.calc-value.big{font-size:1.6rem}
.calc-highlight{border-top:2px solid var(--accent-green);padding-top:18px;margin-top:12px}
.sugestia{background:var(--bg-tertiary);border-radius:12px;padding:18px;text-align:center;margin-top:18px}
.sugestia-value{font-size:2.2rem;font-weight:700;color:var(--accent-yellow)}
.opis-box{background:var(--bg-tertiary);border-radius:12px;padding:18px;white-space:pre-wrap;font-size:0.95rem;line-height:1.7;max-height:280px;overflow-y:auto;margin:18px 0}
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:14px;background:var(--bg-primary);border-radius:12px;margin-bottom:10px}
.toggle-label{font-size:0.95rem}
.toggle{width:48px;height:26px;background:var(--bg-tertiary);border-radius:13px;padding:3px;cursor:pointer;transition:all 0.2s}
.toggle.on{background:var(--accent-blue)}.toggle-knob{width:20px;height:20px;background:#fff;border-radius:50%;transition:all 0.2s}
.toggle.on .toggle-knob{transform:translateX(22px)}
.log-item{display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg-primary);border-radius:10px;margin-bottom:8px}
.log-icon{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.1rem}
.log-icon.sale{background:rgba(34,197,94,0.2)}.log-icon.alert{background:rgba(234,179,8,0.2)}.log-icon.report{background:rgba(59,130,246,0.2)}
.log-content{flex:1;min-width:0}.log-msg{font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log-time{font-size:0.75rem;color:var(--text-muted)}.log-status{font-size:0.75rem;color:var(--accent-green)}
.back{display:block;text-align:center;color:var(--text-muted);text-decoration:none;padding:18px;font-size:0.95rem;transition:color 0.2s}
.back:hover{color:var(--text-primary)}
.bottom-nav{position:fixed;bottom:0;left:0;right:0;background:var(--nav-bg);border-top:1px solid var(--border-color);padding:10px 0;z-index:100}
.bottom-nav-inner{max-width:1600px;margin:0 auto;display:flex;justify-content:space-around}
.nav-item{text-align:center;color:var(--text-muted);text-decoration:none;padding:10px 20px;border-radius:12px;transition:all 0.2s}
.nav-item:hover,.nav-item.active{color:var(--accent-blue);background:rgba(59,130,246,0.1)}
.nav-icon{font-size:1.5rem;margin-bottom:4px}.nav-label{font-size:0.75rem}
.badge{display:inline-block;padding:4px 10px;border-radius:10px;font-size:0.75rem;font-weight:600}
.badge-success{background:rgba(34,197,94,0.2);color:var(--accent-green)}
.badge-warning{background:rgba(234,179,8,0.2);color:var(--accent-yellow)}
.badge-error{background:rgba(239,68,68,0.2);color:var(--accent-red)}
.version-badge{position:fixed;bottom:75px;right:15px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:8px;padding:4px 10px;font-size:0.7rem;color:var(--text-muted);z-index:99}
@media (min-width:1600px){.container{max-width:1600px;padding:30px}.modules-grid{grid-template-columns:repeat(2,1fr)}.tools-grid{grid-template-columns:repeat(4,1fr)}.stats{grid-template-columns:repeat(4,1fr)}.quick-actions{grid-template-columns:repeat(6,1fr);gap:20px}}
@media (min-width:1200px) and (max-width:1599px){.container{max-width:1400px;padding:25px}.modules-grid{grid-template-columns:repeat(2,1fr)}.tools-grid{grid-template-columns:repeat(4,1fr)}}
@media (max-width:1199px){.container{max-width:100%;padding:20px}.modules-grid{grid-template-columns:repeat(2,1fr)}.tools-grid{grid-template-columns:repeat(3,1fr)}}
@media (max-width:900px){.container{max-width:100%;padding:15px}.modules-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,1fr)}.quick-actions{grid-template-columns:repeat(5,1fr)}.tools-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:768px){.container{padding:12px}.stats{grid-template-columns:repeat(2,1fr)}.quick-actions{grid-template-columns:repeat(4,1fr)}.today-value{font-size:1.6rem}.stat-value{font-size:1.4rem}.module-title{font-size:1.05rem}.module-icon{font-size:2rem}.form-row{grid-template-columns:1fr}.theme-toggle{width:40px;height:40px;font-size:1.1rem}}
@media (max-width:480px){.container{padding:10px}.header h1{font-size:1.4rem}.header{padding:18px 0}.quick-actions{grid-template-columns:repeat(3,1fr);gap:8px}.quick-btn{padding:12px 8px}.quick-btn .icon{font-size:1.3rem}.quick-btn .label{font-size:0.65rem}.stats{grid-template-columns:repeat(2,1fr);gap:8px}.stat{padding:12px}.stat-value{font-size:1.3rem}.today-grid{gap:8px}.today-value{font-size:1.4rem}.today-label{font-size:0.7rem}.module{padding:16px}.module-stats{gap:8px}.module-stat{padding:6px 10px;font-size:0.75rem}.tools-grid{grid-template-columns:1fr 1fr}.btn{padding:13px;font-size:0.95rem}.bottom-nav-inner{justify-content:space-between;padding:0 4px}.nav-item{padding:6px 6px}.nav-icon{font-size:1.4rem}.nav-label{font-size:0.7rem}.theme-toggle{top:10px;right:10px;width:36px;height:36px;font-size:1rem}}
@media (max-width:360px){.quick-actions{grid-template-columns:repeat(3,1fr)}.stats{grid-template-columns:1fr 1fr}.today-grid{grid-template-columns:1fr 1fr 1fr}.tools-grid{grid-template-columns:1fr}}
</style>
'''
