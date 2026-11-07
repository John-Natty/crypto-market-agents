"""Accessible inline local chat for the paper trading terminal."""

CHAT_STYLE = r"""
.chat-panel{display:flex;flex-direction:column;min-width:0;width:100%;border:1px solid var(--line,#26323d);border-radius:2px;background:var(--panel,#0e1824);color:var(--text,#d2deec);font:11px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow:hidden;scroll-margin-top:12px}.chat-panel[hidden]{display:none}.chat-panel .chat-header{display:block;padding:10px 12px;border-bottom:1px solid var(--line,#26323d);flex-shrink:0}.chat-panel .chat-header h2{display:flex;align-items:center;gap:7px;font:600 10px/1.4 system-ui,sans-serif;letter-spacing:.055em;text-transform:uppercase;color:var(--text,#d2deec);margin:0}.chat-panel .chat-heading-mark{width:6px;height:6px;background:var(--accent,#70c7f5);flex:none}.chat-panel .chat-header p{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.chat-panel .chat-context{padding:8px 12px;background:var(--panel2,#111f2d);border-bottom:1px solid var(--line,#26323d);font-size:9px;line-height:1.55;color:var(--muted,#75899f);display:flex;justify-content:space-between;align-items:flex-start;gap:12px;overflow-wrap:anywhere}.chat-panel .chat-context span{min-width:0;flex:1}.chat-panel .chat-context strong{max-width:46%;font-weight:400;color:var(--green,#68c8a8);text-align:right;min-width:0}.chat-panel .chat-messages{height:240px;min-height:0;max-width:100%;overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;padding:12px;scrollbar-width:thin;scrollbar-color:#34485b transparent}.chat-panel .chat-welcome{margin:2px 0 4px}.chat-panel .chat-welcome h3{font-size:12px;font-weight:500;line-height:1.5;margin:0 0 8px;color:var(--text,#d2deec)}.chat-panel .chat-welcome p{font:11px/1.65 system-ui,sans-serif;color:var(--muted,#75899f);margin:0 0 13px}.chat-panel .chat-suggestions{display:grid;gap:6px}.chat-panel .chat-suggestion{font:10px/1.5 system-ui,sans-serif;text-align:left;border:1px solid var(--line,#26323d);border-radius:2px;padding:8px 9px;background:var(--panel2,#111f2d);color:var(--text,#d2deec);cursor:pointer;overflow-wrap:anywhere}.chat-panel .chat-suggestion:hover{border-color:#3e6a87;background:#142b3e;color:var(--accent,#70c7f5)}.chat-panel .chat-suggestion:disabled{opacity:.55;cursor:wait}.chat-panel .chat-message{padding:9px 10px;margin:0 0 10px;border:1px solid var(--line,#26323d);border-radius:2px;overflow-wrap:anywhere;min-width:0}.chat-panel .chat-message-user{background:#14283a;border-left:2px solid var(--accent,#70c7f5)}.chat-panel .chat-message-assistant{background:var(--panel2,#111f2d);border-left:2px solid var(--green,#68c8a8)}.chat-panel .chat-message-pending{opacity:.7}.chat-panel .chat-message-label{display:block;margin:0 0 5px;font-size:8px;color:var(--muted,#75899f);text-transform:uppercase;letter-spacing:.055em;line-height:1.6}.chat-panel .chat-message-text{margin:0;font:12px/1.65 system-ui,sans-serif;white-space:pre-wrap;color:var(--text,#d2deec)}.chat-panel .chat-message-meta{margin:8px 0 0;font-size:8px;color:var(--muted,#75899f);line-height:1.6}.chat-panel .chat-composer{padding:8px 12px 10px;border-top:1px solid var(--line,#26323d);background:var(--panel,#0e1824);flex-shrink:0;min-width:0}.chat-panel .chat-status{margin:0 0 6px;min-height:14px;font-size:9px;line-height:1.6;color:var(--muted,#75899f);overflow-wrap:anywhere}.chat-panel .chat-error{margin:0 0 10px;padding:9px 10px;border:1px solid #60424e;border-radius:2px;background:#271c29;color:#e4a5ae;font:11px/1.6 system-ui,sans-serif;overflow-wrap:anywhere}.chat-panel .chat-error[hidden]{display:none}.chat-panel .chat-error p{margin:0 0 8px}.chat-panel .chat-error .chat-button{font-size:9px;padding:5px 8px;background:#332331;color:#e4a5ae;border-color:#60424e}.chat-panel .chat-field-label{display:flex;justify-content:space-between;gap:10px;margin-bottom:6px;font-size:9px;color:var(--text,#d2deec)}.chat-panel .chat-field-label span{color:var(--muted,#75899f);white-space:nowrap}.chat-panel .chat-input{display:block;width:100%;min-width:0;height:60px;min-height:60px;max-height:180px;resize:vertical;padding:9px 10px;border:1px solid #344759;border-radius:2px;background:var(--bg,#090f18);color:var(--text,#d2deec);font:12px/1.6 system-ui,sans-serif}.chat-panel .chat-input:focus-visible{outline:1px solid var(--accent,#70c7f5);outline-offset:1px}.chat-panel .chat-input[readonly]{color:var(--muted,#75899f)}.chat-panel .chat-input::placeholder{color:var(--muted,#75899f)}.chat-panel .chat-form-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px;flex-wrap:wrap}.chat-panel .chat-button{border:1px solid #3e6a87;border-radius:2px;background:#16344a;color:var(--accent,#70c7f5);padding:7px 10px;cursor:pointer;font:10px/1.5 system-ui,sans-serif;min-width:0}.chat-panel .chat-button:hover{background:#20455d;border-color:var(--accent,#70c7f5)}.chat-panel .chat-button:disabled{opacity:.5;cursor:default}.chat-panel .chat-button-secondary{background:var(--panel2,#111f2d);color:var(--muted,#75899f);border-color:var(--line,#26323d);font-size:9px;padding:7px 8px}.chat-panel .chat-button-secondary:hover{background:#182a3b;color:var(--text,#d2deec);border-color:#3b5064}.chat-panel .chat-button:disabled:hover{background:#16344a;border-color:#3e6a87}.chat-panel .chat-button-secondary:disabled:hover{background:var(--panel2,#111f2d);border-color:var(--line,#26323d);color:var(--muted,#75899f)}.chat-panel .chat-help{font-size:8px;line-height:1.7;color:var(--muted,#75899f);margin:8px 0 0}.chat-panel .chat-storage-notice{font-size:9px;line-height:1.65;color:#d2b988;margin:8px 0 0;overflow-wrap:anywhere}.chat-panel button:focus-visible,.chat-panel .chat-messages:focus-visible{outline:1px solid var(--accent,#70c7f5);outline-offset:2px}.chat-panel button,.chat-panel textarea{touch-action:manipulation}
@media(max-width:760px){.chat-panel .chat-messages{height:260px;padding:12px}.chat-panel .chat-input{font-size:16px;min-height:76px}.chat-panel .chat-message-text{font-size:13px}.chat-panel .chat-button,.chat-panel .chat-suggestion{min-height:38px}.chat-panel .chat-header h2{font-size:11px}.chat-panel .chat-welcome p{font-size:12px}.chat-panel .chat-suggestion{font-size:11px}.chat-panel .chat-help{font-size:9px}.chat-panel .chat-context{font-size:10px}.chat-panel .chat-status{font-size:10px}}
@media(prefers-reduced-motion:reduce){.chat-panel{scroll-behavior:auto}}
"""

CHAT_MARKUP = r"""
<section id="chat-panel" class="panel chat-panel" aria-labelledby="chat-title" aria-describedby="chat-description">
<header class="chat-header"><h2 id="chat-title"><span class="chat-heading-mark" aria-hidden="true"></span>Discuter avec l’agent</h2><p id="chat-description">Ses décisions, votre portefeuille.</p></header>
<div class="chat-context"><span id="chat-model">IA locale · en attente du suivi</span><strong id="chat-portfolio">Simulation</strong></div>
<div id="chat-messages" class="chat-messages" role="log" aria-label="Messages de la discussion" aria-live="polite" aria-relevant="additions text" tabindex="0"></div>
<div class="chat-composer"><p id="chat-status" class="chat-status" role="status" aria-live="polite">Connexion au portefeuille…</p><div id="chat-error" class="chat-error" role="alert" hidden><p id="chat-error-message"></p><button type="button" id="chat-retry" class="chat-button chat-button-secondary">Réessayer</button></div>
<form id="chat-form"><label for="chat-input" class="chat-field-label">Votre question <span id="chat-counter">0 / 2 000</span></label><textarea id="chat-input" class="chat-input" name="message" rows="2" maxlength="2000" placeholder="Pourquoi l’agent attend-il ?" aria-describedby="chat-help chat-counter"></textarea><div class="chat-form-actions"><button type="button" id="chat-clear" class="chat-button chat-button-secondary" disabled>Effacer la discussion</button><button type="submit" id="chat-send" class="chat-button" disabled>Envoyer ↗</button></div><p id="chat-help" class="chat-help">Ctrl / ⌘ + Entrée pour envoyer · Lecture seule.</p><p id="chat-storage-notice" class="chat-storage-notice" hidden></p></form></div>
</section>
"""

CHAT_SCRIPT = r"""<script>
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('chat-panel'), input = $('chat-input');
  const maxMessage = 2000, maxReply = 4000, maxEntries = 20;
  const dateFormat = new Intl.DateTimeFormat('fr-FR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  const suggestions = ['Pourquoi l’agent attend-il ?', 'Explique-moi la dernière décision.', 'Où en est mon portefeuille ?'];
  let entries = [], portfolioKey = null, pending = false, pendingMessage = '';
  let controller = null, requestSerial = 0;
  const text = (id, value) => { $(id).textContent = value; };
  const setStatus = value => text('chat-status',value);
  const element = (tag, content, className) => {
    const node = document.createElement(tag);
    if (content !== undefined) node.textContent = content;
    if (className) node.className = className;
    return node;
  };
  function validDate(value) { return typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value)); }
  function storeConversation() {
    if (!portfolioKey) return;
    try {
      sessionStorage.setItem(portfolioKey,JSON.stringify({version:1,entries}));
      $('chat-storage-notice').hidden = true;
    } catch (_) {
      text('chat-storage-notice','La discussion reste disponible sur cette page, mais ne peut pas être conservée après son rechargement.');
      $('chat-storage-notice').hidden = false;
    }
  }
  function loadConversation() {
    entries = [];
    try {
      const saved = JSON.parse(sessionStorage.getItem(portfolioKey) || 'null');
      if (!saved || saved.version !== 1 || !Array.isArray(saved.entries)) return;
      const recent = saved.entries.slice(-maxEntries);
      if (recent.length % 2 || recent.some((entry,index) => !entry || entry.role !== (index % 2 ? 'assistant' : 'user') || typeof entry.content !== 'string' || !entry.content.trim() || entry.content.length > (entry.role === 'user' ? maxMessage : maxReply))) return;
      entries = recent.map(entry => ({role:entry.role,content:entry.content,model:typeof entry.model === 'string' ? entry.model.slice(0,120) : '',context_at:validDate(entry.context_at) ? entry.context_at : ''}));
      $('chat-storage-notice').hidden = true;
    } catch (_) {
      text('chat-storage-notice','L’historique de cette session n’est pas disponible. Vous pouvez commencer une nouvelle discussion.');
      $('chat-storage-notice').hidden = false;
    }
  }
  function updateControls() {
    const length = input.value.length;
    text('chat-counter',length.toLocaleString('fr-FR') + ' / 2 000');
    input.readOnly = pending;
    $('chat-send').disabled = pending || !portfolioKey || !input.value.trim() || length > maxMessage;
    text('chat-send',pending ? 'Réponse en cours…' : 'Envoyer ↗');
    $('chat-clear').disabled = pending || (!entries.length && !input.value && $('chat-error').hidden);
    $('chat-retry').disabled = pending || !portfolioKey || !input.value.trim() || length > maxMessage;
    for (const button of panel.querySelectorAll('.chat-suggestion')) button.disabled = pending;
  }
  function renderMessages() {
    const fragment = document.createDocumentFragment();
    if (!entries.length && !pending) {
      const welcome = element('div',undefined,'chat-welcome');
      welcome.append(element('h3','Que souhaitez-vous comprendre ?'));
      welcome.append(element('p','Posez une question sur le portefeuille simulé, la dernière décision ou les éléments que l’agent observe. Le contexte est actualisé à chaque question.'));
      const list = element('div',undefined,'chat-suggestions');
      for (const question of suggestions) {
        const button = element('button',question,'chat-suggestion');
        button.type = 'button';
        button.addEventListener('click',() => { input.value = question; hideError(); updateControls(); input.focus(); });
        list.append(button);
      }
      welcome.append(list); fragment.append(welcome);
    }
    const visible = pending ? [...entries,{role:'user',content:pendingMessage,pending:true}] : entries;
    for (const entry of visible) {
      const article = element('article',undefined,'chat-message chat-message-' + entry.role + (entry.pending ? ' chat-message-pending' : ''));
      article.append(element('span',entry.role === 'user' ? 'Vous' : 'Agent' + (entry.model ? ' · ' + entry.model : ''),'chat-message-label'));
      article.append(element('p',entry.content,'chat-message-text'));
      if (entry.role === 'assistant' && validDate(entry.context_at)) article.append(element('p','Contexte du ' + dateFormat.format(new Date(entry.context_at)),'chat-message-meta'));
      fragment.append(article);
    }
    $('chat-messages').replaceChildren(fragment);
    $('chat-messages').scrollTop = $('chat-messages').scrollHeight;
  }
  function hideError() { $('chat-error').hidden = true; text('chat-error-message',''); }
  function showError(message) { text('chat-error-message',message); $('chat-error').hidden = false; }
  function updateContext(event) {
    const data = event.detail;
    if (!data || data.ok !== true || typeof data.portfolio_name !== 'string' || !data.portfolio_name) return;
    const identity = typeof data.portfolio_id === 'string' && data.portfolio_id ? data.portfolio_id : data.portfolio_name;
    const nextKey = 'crypto-market-agents:chat:v1:' + location.origin + ':' + identity;
    const runtime = data.runtime || {};
    text('chat-model',runtime.provider === 'ollama' && typeof runtime.model === 'string' ? 'IA locale · ' + runtime.model : 'IA locale · disponibilité vérifiée à l’envoi');
    text('chat-portfolio',data.portfolio_name);
    if (nextKey === portfolioKey) return;
    const changed = portfolioKey !== null;
    requestSerial++;
    if (controller) controller.abort();
    controller = null; pending = false; pendingMessage = '';
    portfolioKey = nextKey;
    if (changed) input.value = '';
    hideError(); loadConversation(); renderMessages(); updateControls();
    setStatus(entries.length ? 'Discussion restaurée pour ce portefeuille.' : 'Prêt à discuter de ce portefeuille.');
  }
  async function sendMessage(event) {
    if (event) event.preventDefault();
    const message = input.value.trim();
    if (pending || !portfolioKey || !message || input.value.length > maxMessage) return;
    const serial = ++requestSerial;
    const requestController = new AbortController();
    controller = requestController;
    const timeout = setTimeout(() => requestController.abort(),135000);
    const history = [];
    let remaining = 12000 - message.length;
    for (let index = entries.length - 2; index >= 0 && history.length < 8; index -= 2) {
      const pair = entries.slice(index,index + 2).map(entry => ({role:entry.role,content:entry.content}));
      const size = pair.reduce((total,entry) => total + entry.content.length,0);
      if (size > remaining) break;
      history.unshift(...pair); remaining -= size;
    }
    pending = true; pendingMessage = message; hideError(); renderMessages(); updateControls();
    setStatus('L’agent prépare sa réponse… Le suivi continue en arrière-plan.');
    try {
      const response = await fetch('/api/chat',{method:'POST',cache:'no-store',signal:requestController.signal,headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify({message,history})});
      const data = await response.json();
      if (serial !== requestSerial) return;
      if (!response.ok || !data || data.ok !== true) throw new Error(data && typeof data.message === 'string' && data.message ? data.message : 'L’agent n’a pas pu répondre. Réessayez dans un instant.');
      if (typeof data.reply !== 'string' || !data.reply.trim() || data.reply.length > maxReply) throw new Error('La réponse de l’agent est vide ou inexploitable. Votre question est conservée.');
      entries.push({role:'user',content:message},{role:'assistant',content:data.reply,model:typeof data.model === 'string' ? data.model.slice(0,120) : '',context_at:validDate(data.context_at) ? data.context_at : ''});
      entries = entries.slice(-maxEntries);
      input.value = ''; storeConversation();
      setStatus('Réponse reçue. Vous pouvez poser une autre question.');
    } catch (error) {
      if (serial !== requestSerial) return;
      const message = error.name === 'AbortError' ? 'L’agent met trop de temps à répondre. Votre question est conservée ; vous pouvez réessayer.' : error instanceof SyntaxError ? 'Le serveur a renvoyé une réponse illisible. Votre question est conservée.' : error instanceof TypeError ? 'Impossible de joindre l’agent. Vérifiez que le serveur local fonctionne, puis réessayez.' : error.message || 'La discussion est momentanément indisponible.';
      showError(message); setStatus('Aucune réponse reçue. Votre question est conservée.');
    } finally {
      clearTimeout(timeout);
      if (serial === requestSerial) {
        pending = false; pendingMessage = ''; controller = null;
        renderMessages(); updateControls();
      }
    }
  }
  for (const trigger of document.querySelectorAll('#chat-open,[data-chat-open],a[href="#chat-panel"]')) {
    trigger.addEventListener('click',event => {
      event.preventDefault();
      panel.scrollIntoView({block:'nearest',behavior:'instant'});
      input.focus({preventScroll:true});
      $('chat-messages').scrollTop = $('chat-messages').scrollHeight;
    });
  }
  $('chat-form').addEventListener('submit',sendMessage);
  $('chat-retry').addEventListener('click',sendMessage);
  input.addEventListener('input',() => { hideError(); updateControls(); });
  input.addEventListener('keydown',event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) { event.preventDefault(); sendMessage(); } });
  $('chat-clear').addEventListener('click',() => {
    if (pending) return;
    entries = []; input.value = ''; hideError(); storeConversation(); renderMessages(); updateControls();
    setStatus('Discussion effacée. Posez une nouvelle question.'); input.focus();
  });
  document.addEventListener('paper-snapshot',updateContext);
  renderMessages(); updateControls();
})();
</script>"""
