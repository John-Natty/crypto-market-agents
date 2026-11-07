"""Self-contained, read-only live view of one paper portfolio."""

from .chat_view import CHAT_MARKUP, CHAT_SCRIPT, CHAT_STYLE
from .live_layout import LIVE_SHELL


def render_live_dashboard() -> str:
    """Render the Vector terminal with live portfolio data and an inline chat."""
    return (
        LIVE_SHELL.replace("/* CHAT_STYLE */", CHAT_STYLE)
        .replace("<!-- CHAT_MARKUP -->", CHAT_MARKUP)
        .replace("<!-- LIVE_SCRIPT -->", CHAT_SCRIPT + _LIVE_SCRIPT)
    )


_LIVE_SCRIPT = r"""<script>
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const eur = new Intl.NumberFormat('fr-FR', {style:'currency', currency:'EUR', minimumFractionDigits:2, maximumFractionDigits:2});
  const fineEur = new Intl.NumberFormat('fr-FR', {style:'currency', currency:'EUR', minimumFractionDigits:2, maximumFractionDigits:4});
  const decimal = new Intl.NumberFormat('fr-FR', {maximumFractionDigits:8});
  const dateFormat = new Intl.DateTimeFormat('fr-FR', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'});
  const timeFormat = new Intl.DateTimeFormat('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  const actionLabels = {buy:'Acheter', sell:'Vendre', hold:'Attendre'};
  const outcomeLabels = {executed:'Opération simulée', held:'Aucune opération', rejected:'Proposition refusée'};
  let snapshot = null;
  let connected = false;
  let serverOffset = 0;
  let lastSync = null;
  let ledgerSignature = '';
  let chartSignature = '';
  let chartMode = 'market';
  function text(id, value) { const el = $(id), content = value == null ? '—' : String(value); if (el.textContent !== content) el.textContent = content; }
  function number(value) {
    if (value == null || value === '' || typeof value === 'boolean') return null;
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  }
  function money(value, fine=false) { const n = number(value); return n === null ? '—' : (fine ? fineEur : eur).format(n); }
  function priceMoney(value) { const n = number(value); return n === null ? '—' : new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',minimumFractionDigits:2,maximumFractionDigits:Math.abs(n)<1?8:Math.abs(n)<100?4:2}).format(n); }
  function parsedDate(value) { if (!value) return null; const d = new Date(value); return Number.isFinite(d.getTime()) ? d : null; }
  function date(value, short=false) { const d = parsedDate(value); return d ? (short ? timeFormat : dateFormat).format(d) : '—'; }
  function duration(seconds) {
    if (!Number.isFinite(seconds)) return '—';
    const s = Math.max(0, Math.floor(seconds));
    if (s < 60) return s + ' s';
    if (s < 3600) return Math.floor(s/60) + ' min ' + (s%60) + ' s';
    if (s < 86400) return Math.floor(s/3600) + ' h ' + Math.floor((s%3600)/60) + ' min';
    return Math.floor(s/86400) + ' j ' + Math.floor((s%86400)/3600) + ' h';
  }
  function signedMoney(value) { const n = number(value); return n === null ? '—' : (n > 0 ? '+' : '') + money(n); }
  function tone(element, value) { element.classList.remove('positive','negative','neutral'); const n = number(value); element.classList.add(n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral'); }
  function syncMirrors() {
    const states = ['positive','negative','neutral','action-buy','action-sell','action-hold','action-unknown','connected','offline'];
    for (const mirror of document.querySelectorAll('[data-mirror]')) {
      const source = $(mirror.dataset.mirror);
      if (!source) continue;
      if (mirror.textContent !== source.textContent) mirror.textContent = source.textContent;
      for (const state of states) mirror.classList.toggle(state, source.classList.contains(state));
    }
  }
  function setConnection(ok) {
    connected = ok;
    $('connection').classList.toggle('connected', ok);
    $('connection').classList.toggle('offline', !ok);
    text('connection-text', ok ? 'Suivi connecté' : 'Suivi indisponible');
  }
  function setNotice(message, fatal=false) {
    const el = $('notice'); el.hidden = !message; el.textContent = message || ''; el.classList.toggle('fatal', fatal);
  }
  function node(tag, content, className) {
    const result = document.createElement(tag);
    if (content != null) result.textContent = String(content);
    if (className) result.className = className;
    return result;
  }
  function updateMetrics(data) {
    const m = data.metrics || {}, limits = data.limits || {}, runtime = data.runtime || {};
    text('equity', money(m.equity_eur));
    text('initial', 'Capital initial : ' + money(m.initial_cash_eur));
    text('pnl', signedMoney(m.net_pnl_eur)); tone($('pnl'), m.net_pnl_eur);
    const pct = number(m.net_pnl_percent);
    text('pnl-percent', pct === null ? 'Depuis le début de la simulation' : (pct > 0 ? '+' : '') + decimal.format(pct) + ' % depuis le début');
    text('cash', money(m.cash_eur)); text('position', money(m.position_value_eur));
    const asset = typeof m.market === 'string' ? m.market.split('-')[0] : '';
    text('quantity', number(m.quantity) === null ? 'Quantité : —' : decimal.format(Number(m.quantity)) + ' ' + asset);
    text('market', m.market);
    text('market-name', m.market ? m.market.replace('-', ' / ') : 'Marché observé');
    text('market-price', priceMoney(m.last_bid_eur));
    text('allocation-cash', money(m.cash_eur));
    text('allocation-position', money(m.position_value_eur));
    text('allocation-quantity', number(m.quantity) === null ? '—' : decimal.format(Number(m.quantity)) + ' ' + asset);
    const equity = number(m.equity_eur), cash = number(m.cash_eur), position = number(m.position_value_eur);
    const sharesKnown = equity !== null && equity > 0 && cash !== null && position !== null;
    const cashShare = sharesKnown ? Math.max(0, Math.min(100, cash/equity*100)) : 0;
    const positionShare = sharesKnown ? Math.max(0, Math.min(100, position/equity*100)) : 0;
    const percent = new Intl.NumberFormat('fr-FR',{maximumFractionDigits:1});
    text('allocation-cash-percent', sharesKnown ? percent.format(cashShare) + ' %' : '—');
    text('allocation-position-percent', sharesKnown ? percent.format(positionShare) + ' %' : '—');
    $('allocation-cash-bar').style.width = cashShare + '%';
    $('allocation-position-bar').style.width = positionShare + '%';
    text('provider', runtime.provider === 'ollama' ? (runtime.model || 'IA locale') : runtime.provider === 'rules' ? 'Règles témoins · sans IA' : 'Non observé');
    text('decision-step-label', runtime.provider === 'ollama' ? 'Décision de l’IA' : 'Décision');
    text('source', m.source === 'bitvavo' ? 'Bitvavo · cours publics' : m.source === 'mock' ? 'Cours fictifs de démonstration' : m.source);
    text('cycle', Number.isInteger(runtime.cycle) && runtime.cycle > 0 ? runtime.cycle : '—');
    const interval = number(runtime.interval_seconds);
    text('interval', interval === null ? 'Non observée' : interval === 0 ? 'Sans pause' : duration(interval));
    text('max-trade', money(limits.max_trade_eur)); text('max-position', money(limits.max_position_eur)); text('max-loss', money(limits.max_loss_eur));
    text('fees', money(m.fees_paid_eur, true)); text('drawdown', money(m.max_drawdown_eur, true)); text('fills', m.fills);
    const fee = number(limits.fee_rate), slip = number(limits.slippage_bps);
    text('fee-assumptions', (fee === null ? '—' : decimal.format(fee*100) + ' %') + ' / ' + (slip === null ? '—' : decimal.format(slip/100) + ' %'));
    $('risk-pause').hidden = data.buying_paused !== true;
    text('decision-count', m.decisions || 0);
  }
  function updateActivity() {
    if (!snapshot) return;
    const runtime = snapshot.runtime || {}, m = snapshot.metrics || {}, freshness = snapshot.freshness || {};
    const now = Date.now() + serverOffset;
    const phase = runtime.phase || 'unknown';
    let title = '', description = '', active = null;
    if (!connected) { title = 'Connexion interrompue'; description = 'L’activité actuelle ne peut pas être confirmée. Les dernières valeurs reçues restent affichées.'; }
    else if (runtime.stale && phase !== 'stopped') { title = 'Signal d’activité interrompu'; description = 'Le dernier état du programme est trop ancien pour confirmer qu’il fonctionne encore.'; }
    else if (runtime.available === false && !runtime.last_error) { title = 'Aucune activité observée'; description = 'Le portefeuille est consultable. Aucun programme de simulation actif n’est confirmé.'; }
    else if (phase === 'error') { title = 'Cycle en erreur'; description = 'Le cycle n’a pas pu se terminer. Aucun succès n’est supposé.'; }
    else if (phase === 'starting') { title = 'Démarrage de la simulation'; description = 'Le programme prépare le prochain cycle d’observation.'; }
    else if (phase === 'market') { title = 'Lecture des cours'; description = 'Le programme récupère les prix pour ce cycle.'; active = 'market'; }
    else if (phase === 'deciding') { title = runtime.provider === 'ollama' ? 'L’IA examine le marché' : 'Évaluation des règles témoins'; description = runtime.provider === 'ollama' ? 'Le modèle local prépare une proposition à partir du portefeuille et des cours.' : 'La stratégie de référence prépare une proposition, sans modèle d’IA.'; active = 'deciding'; }
    else if (phase === 'executing') { title = 'Vérification de la proposition'; description = 'Les limites sont contrôlées avant toute opération simulée.'; active = 'executing'; }
    else if (phase === 'waiting') { title = 'En attente du prochain cycle'; description = runtime.last_error ? 'Le programme attend sa prochaine tentative.' : 'Le dernier cycle est terminé. Le programme attend sa prochaine exécution.'; active = 'waiting'; }
    else if (phase === 'stopped') { title = 'Simulation arrêtée'; description = 'La session est terminée. Cette page continue de suivre le portefeuille.'; }
    else { title = 'Activité non confirmée'; description = 'Le portefeuille est disponible, mais l’état du programme n’est pas connu.'; }
    text('activity-title', title); text('activity-description', description);
    $('runtime-error').hidden = !runtime.last_error;
    text('runtime-error', runtime.last_error ? 'Dernière erreur : ' + runtime.last_error : '');
    for (const name of ['market','deciding','executing','waiting']) $('step-' + name).classList.toggle('active', name === active);
    const next = parsedDate(runtime.next_run_at);
    if (!connected || runtime.stale || !runtime.running || !next) text('next-run', 'Non planifiée / non confirmée');
    else { const seconds = (next.getTime()-now)/1000; text('next-run', seconds > 0 ? 'Dans ' + duration(seconds) : 'Échéance atteinte · en attente'); }
    const observed = parsedDate(m.last_observed_at);
    const age = observed ? Math.max(0, (now-observed.getTime())/1000) : null;
    const stale = freshness.stale || (age !== null && age > (number(freshness.max_age_seconds) || 120));
    text('observed', 'Dernier cours : ' + priceMoney(m.last_bid_eur) + ' · ' + date(m.last_observed_at));
    text('freshness', age === null ? 'Aucun cours observé' : (stale ? 'Cours ancien · ' : 'Observé il y a ') + duration(age));
    $('freshness').classList.toggle('negative', Boolean(stale));
    text('chart-subtitle', chartMode === 'market' ? (stale ? 'Cours ancien · dernières observations enregistrées' : 'Cours acheteur (bid) observé à chaque cycle') : (stale ? 'Valeurs au dernier cycle · cours ancien, valorisation indicative' : 'Valorisation au dernier cours observé · frais déjà payés inclus'));
    if (lastSync) text('sync', 'Dernière réception à ' + date(lastSync, true));
    syncMirrors();
  }
  function renderDecisions(data) {
    const decisions = Array.isArray(data.decisions) ? data.decisions : [];
    const signature = JSON.stringify(decisions);
    if (signature === ledgerSignature) return;
    ledgerSignature = signature;
    const rows = document.createDocumentFragment();
    for (const event of decisions) {
      const tr = node('tr');
      const when = node('td', date(event.timestamp));
      when.append(node('span', event.provider === 'rules' ? 'Règles témoins' : event.provider || '—', 'cell-sub'));
      const action = node('td');
      const actionKey = Object.hasOwn(actionLabels, event.action) ? event.action : 'unknown';
      action.append(node('span', actionLabels[actionKey] || 'Inconnue', 'action-pill action-' + actionKey));
      action.append(node('span', outcomeLabels[event.status] || event.status || '—', 'cell-sub'));
      const amount = node('td');
      if (event.status === 'executed') {
        amount.append(node('span', money(event.notional_eur, true), 'amount'));
        amount.append(node('span', 'Frais : ' + money(event.fee_eur, true), 'cell-sub'));
      } else {
        amount.append(node('span', event.action === 'hold' ? '—' : money(event.requested_amount_eur, true), 'amount'));
        amount.append(node('span', event.action === 'hold' ? 'Aucun échange' : 'Montant demandé', 'cell-sub'));
      }
      const reason = node('td');
      reason.append(node('p', event.decision_reason || event.reason || 'Raison non renseignée.', 'reason-main'));
      if (event.reason && event.decision_reason && event.reason !== event.decision_reason) reason.append(node('p', 'Contrôle : ' + event.reason, 'engine-reason'));
      tr.append(when, action, amount, reason); rows.append(tr);
    }
    $('decision-rows').replaceChildren(rows);
    const recent = document.createDocumentFragment();
    for (const event of decisions.slice(0,3)) {
      const key = Object.hasOwn(actionLabels, event.action) ? event.action : 'unknown';
      const item = node('article', null, 'recent-event');
      const heading = node('div', null, 'recent-event-head');
      const when = node('time', date(event.timestamp), 'recent-time');
      if (parsedDate(event.timestamp)) when.dateTime = new Date(event.timestamp).toISOString();
      heading.append(node('span', actionLabels[key] || 'Inconnue', 'action-pill action-' + key), when);
      item.append(heading, node('span', outcomeLabels[event.status] || event.status || '—', 'cell-sub'), node('p', event.decision_reason || event.reason || 'Raison non renseignée.', 'recent-reason'));
      if (event.status === 'rejected' && event.reason && event.reason !== event.decision_reason) item.append(node('p', 'Contrôle : ' + event.reason, 'engine-reason'));
      recent.append(item);
    }
    if (!decisions.length) recent.append(node('p', 'Les prochaines décisions apparaîtront ici.', 'empty'));
    $('recent-activity').replaceChildren(recent);
    $('journal-empty').hidden = decisions.length > 0;
    $('journal-scroll').hidden = decisions.length === 0;
    text('journal-foot', decisions.length ? decisions.length + ' décisions affichées, les plus récentes en premier · Aucun ordre réel.' : 'Aucun ordre réel n’est envoyé.');
    const last = decisions[0];
    const latestKey = last && Object.hasOwn(actionLabels, last.action) ? last.action : 'unknown';
    text('latest-action-metric', last ? actionLabels[latestKey] || 'Inconnue' : '—');
    $('latest-action-metric').dataset.action = latestKey;
    text('latest-decision-meta', last ? (outcomeLabels[last.status] || last.status || '—') + ' · ' + date(last.timestamp,true) : 'Aucune décision enregistrée');
    if (!last) { text('last-action','Aucune décision'); $('last-action').className = 'action-pill action-unknown'; text('last-time',''); text('last-reason','L’agent n’a pas encore pris de décision.'); text('last-result','—'); return; }
    const key = Object.hasOwn(actionLabels, last.action) ? last.action : 'unknown';
    text('last-action', actionLabels[key] || 'Inconnue'); $('last-action').className = 'action-pill action-' + key;
    text('last-time', date(last.timestamp)); text('last-reason', last.decision_reason || last.reason || 'Raison non renseignée.');
    text('last-result', (outcomeLabels[last.status] || last.status || '—') + (last.status === 'executed' ? ' · ' + money(last.notional_eur, true) + ' hors frais' : '') + (last.status === 'rejected' && last.decision_reason && last.reason !== last.decision_reason ? ' · ' + last.reason : ''));
  }
  function svgNode(tag, attrs={}, content=null) {
    const el = document.createElementNS('http://www.w3.org/2000/svg',tag);
    for (const [key,value] of Object.entries(attrs)) el.setAttribute(key,String(value));
    if (content !== null) el.textContent = content;
    return el;
  }
  function renderChart(data) {
    const isMarket = chartMode === 'market';
    const history = isMarket ? (Array.isArray(data.market_history) ? data.market_history : []) : (Array.isArray(data.history) ? data.history : []);
    const initial = isMarket ? null : number(data.metrics.initial_cash_eur);
    const chartWidth = Math.max(220, Math.round($('equity-chart').getBoundingClientRect().width));
    const chartHeight = Math.max(220, Math.round($('equity-chart').getBoundingClientRect().height));
    const signature = JSON.stringify([chartMode,history,initial,chartWidth,chartHeight,data.metrics.market]);
    if (signature === chartSignature) return;
    chartSignature = signature;
    $('equity-chart').setAttribute('viewBox', '0 0 ' + chartWidth + ' ' + chartHeight);
    text('chart-title', isMarket ? 'Observation du marché' : 'Évolution du portefeuille');
    text('chart-legend-main', isMarket ? 'Cours ' + (data.metrics.market || '') : 'Portefeuille');
    text('chart-legend-baseline', 'Capital initial');
    $('chart-baseline-legend').hidden = isMarket;
    for (const mode of ['market','portfolio']) {
      const active = mode === chartMode;
      $('chart-' + mode).classList.toggle('active', active);
      $('chart-' + mode).setAttribute('aria-pressed', String(active));
    }
    const points = history.map(p => ({time:parsedDate(p.timestamp),value:number(isMarket ? p.bid_eur : p.equity_eur)})).filter(p => p.time && p.value !== null);
    text('chart-point-count', points.length + (points.length === 1 ? ' observation' : ' observations'));
    const drawing = document.createDocumentFragment();
    if (!points.length) {
      drawing.append(svgNode('text',{x:chartWidth/2,y:chartHeight/2,'text-anchor':'middle'},'En attente du premier cours.'));
      $('chart-drawing').replaceChildren(drawing);
      $('equity-chart').setAttribute('aria-label', 'Aucun cours enregistré pour le moment.');
      return;
    }
    const compactEur = new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',notation:'compact',maximumFractionDigits:2});
    const tickLabel = value => Math.abs(value)>=1e6 ? compactEur.format(value) : isMarket ? priceMoney(value) : money(value);
    const left = 10, right = chartWidth-100, top = 20, bottom = chartHeight-42;
    const values = points.map(p => p.value); if (initial !== null) values.push(initial);
    const low = Math.min(...values), high = Math.max(...values);
    const minimumPad = isMarket ? Math.max(Math.abs(high)*.0001,1e-8) : Math.max(Math.abs(initial || high)*.002,.05);
    const pad = Math.max((high-low)*.22,minimumPad), min = low-pad, max = high+pad;
    const y = v => bottom-((v-min)/(max-min))*(bottom-top);
    const t0 = points[0].time.getTime(), t1 = points[points.length-1].time.getTime();
    const x = (p,i) => points.length === 1 ? (left+right)/2 : t1 > t0 ? left+(p.time.getTime()-t0)/(t1-t0)*(right-left) : left+i/(points.length-1)*(right-left);
    for (let i=0;i<4;i++) {
      const value = min+(max-min)*i/3, yy = y(value);
      drawing.append(svgNode('line',{x1:left,x2:right,y1:yy,y2:yy,stroke:'#223141','stroke-width':1,'stroke-dasharray':'2 5'}));
      drawing.append(svgNode('text',{x:right+12,y:yy+3},tickLabel(value)));
    }
    for (let i=0;i<5;i++) {
      const xx = left+(right-left)*i/4;
      drawing.append(svgNode('line',{x1:xx,x2:xx,y1:top,y2:bottom,stroke:'#223141','stroke-width':1,'stroke-dasharray':'2 5'}));
    }
    if (initial !== null) drawing.append(svgNode('line',{x1:left,x2:right,y1:y(initial),y2:y(initial),stroke:'#657d95','stroke-width':1,'stroke-dasharray':'6 5'}));
    if (points.length > 1) {
      const path = points.map((p,i) => (i?'L':'M')+x(p,i).toFixed(2)+','+y(p.value).toFixed(2)).join(' ');
      drawing.append(svgNode('path',{d:path+' L'+x(points[points.length-1],points.length-1)+','+bottom+' L'+x(points[0],0)+','+bottom+' Z',fill:'url(#chart-fill)'}));
      drawing.append(svgNode('path',{d:path,fill:'none',stroke:'#70c7f5','stroke-width':1.8,'stroke-linecap':'round','stroke-linejoin':'round'}));
    }
    const last = points[points.length-1];
    if (points[0].time.toDateString() !== last.time.toDateString()) {
      const day = new Intl.DateTimeFormat('fr-FR',{day:'2-digit',month:'2-digit',year:'numeric'});
      text('chart-point-count', points.length + ' observations · ' + day.format(points[0].time) + ' — ' + day.format(last.time));
    }
    drawing.append(svgNode('line',{x1:left,x2:right,y1:y(last.value),y2:y(last.value),stroke:'#70c7f5','stroke-opacity':.3,'stroke-width':1,'stroke-dasharray':'4 4'}));
    drawing.append(svgNode('circle',{cx:x(last,points.length-1),cy:y(last.value),r:3.5,fill:'#70c7f5',stroke:'#0c1723','stroke-width':2}));
    const shortDates = chartWidth < 600;
    drawing.append(svgNode('text',{x:left,y:chartHeight-12},date(points[0].time,shortDates)));
    if (points.length > 1) drawing.append(svgNode('text',{x:right,y:chartHeight-12,'text-anchor':'end'},date(last.time,shortDates)));
    $('chart-drawing').replaceChildren(drawing);
    $('equity-chart').setAttribute('aria-label',(isMarket ? 'Cours ' + data.metrics.market : 'Valeur du portefeuille') + ', de '+tickLabel(points[0].value)+' à '+tickLabel(last.value)+', sur '+points.length+' observations.'+(initial !== null ? ' Capital initial : '+money(initial)+'.' : ''));
  }
  async function poll() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(),8000);
    try {
      const response = await fetch('/api/paper',{cache:'no-store',signal:controller.signal,headers:{Accept:'application/json'}});
      const data = await response.json();
      if (!response.ok || data.ok !== true) {
        const message = typeof data.message === 'string' ? data.message : 'Le portefeuille est momentanément indisponible.';
        throw new Error(message);
      }
      if (!data.metrics || !data.runtime || !Array.isArray(data.decisions) || !Array.isArray(data.history) || !Array.isArray(data.market_history)) throw new Error('Les données de suivi reçues sont incomplètes.');
      snapshot = data;
      document.dispatchEvent(new CustomEvent('paper-snapshot',{detail:data}));
      const serverDate = parsedDate(data.server_time); if (serverDate) serverOffset = serverDate.getTime()-Date.now();
      lastSync = new Date(); setConnection(true); setNotice('');
      updateMetrics(data); renderDecisions(data); renderChart(data); updateActivity();
    } catch (error) {
      setConnection(false);
      const message = error.name === 'AbortError' ? 'Le serveur de suivi ne répond pas.' : error instanceof SyntaxError ? 'Le serveur a renvoyé des données illisibles.' : error instanceof TypeError ? 'Impossible de joindre le serveur de suivi.' : error.message || 'Le suivi est indisponible.';
      setNotice(message + (snapshot ? ' Les dernières données reçues restent affichées ; leur mise à jour est interrompue.' : ' Une nouvelle tentative aura lieu automatiquement.'), !snapshot);
      if (!snapshot) { text('activity-title','Suivi indisponible'); text('activity-description','Aucune donnée exploitable n’a encore été reçue.'); }
      updateActivity();
      syncMirrors();
    } finally {
      clearTimeout(timeout);
      setTimeout(poll,2000);
    }
  }
  for (const mode of ['market','portfolio']) {
    $('chart-' + mode).addEventListener('click', () => {
      chartMode = mode;
      chartSignature = '';
      for (const option of ['market','portfolio']) {
        $('chart-' + option).classList.toggle('active',option === mode);
        $('chart-' + option).setAttribute('aria-pressed',String(option === mode));
      }
      if (snapshot) { renderChart(snapshot); updateActivity(); }
    });
  }
  const resizeChart = () => { if (snapshot) renderChart(snapshot); };
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resizeChart).observe($('equity-chart'));
  else window.addEventListener('resize',resizeChart);
  setInterval(updateActivity,1000);
  poll();
})();
</script>"""
