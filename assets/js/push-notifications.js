(function () {
  'use strict';

  var notifications = [];
  var unreadCount = 0;
  var socket = null;
  var connected = false;
  var notifPanel = null;
  var panelOpen = false;

  // ── Storage helpers ──────────────────────────────────────────────────────

  function loadNotifications(){ return notifications; }

  function addNotification(notif){ notifications.unshift(notif); unreadCount++; updateBadge(); renderNotifList(); }

  async function fetchNotifications(){
    try {
      var res = await fetch('/api/notifications', {credentials:'include'});
      var data = await res.json();
      if (data && data.success) {
        notifications = data.notifications.map(function(n){return {id:n.id,type:(n.category||'system').toLowerCase(),icon:'📢',title:n.title,detail:n.message,badgeLabel:n.category||'System',badgeClass:'badge-system',ts:Date.parse(n.created_at||new Date().toISOString()),read:n.read};});
        unreadCount = notifications.filter(function(n){return !n.read;}).length;
        updateBadge();
        renderNotifList();
      }
    } catch(e){}
  }

  async function markAllRead(){
    try { await fetch('/api/notifications/read-all', {method:'POST', credentials:'include'}); } catch(e){}
    notifications = notifications.map(function(n){ n.read=true; return n;});
    unreadCount = 0; updateBadge(); renderNotifList();
  }

  function clearAll() { notifications = []; unreadCount = 0; updateBadge(); renderNotifList(); }

  // ── Badge ────────────────────────────────────────────────────────────────

  function updateBadge() {
    var badge = document.getElementById('notif-badge');
    if (!badge) return;
    badge.textContent = unreadCount > 99 ? '99+' : String(unreadCount);
    badge.style.display = unreadCount > 0 ? 'flex' : 'none';
  }

  // ── Web Notifications API ────────────────────────────────────────────────

  function requestNotifPermission() {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }

  function sendBrowserNotif(title, body, icon) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    if (document.visibilityState === 'visible') return;
    try {
      new Notification(title, {
        body: body,
        icon: icon || '/assets/icons/icon-192.png',
        badge: '/assets/icons/icon-192.png',
        tag: 'gtavcad-' + Date.now(),
        requireInteraction: false,
      });
    } catch (e) {}
  }

  function vibrate(pattern) {
    if (navigator.vibrate) {
      try { navigator.vibrate(pattern); } catch (e) {}
    }
  }

  // ── Notification panel ──────────────────────────────────────────────────

  function buildPanel() {
    if (document.getElementById('push-notif-panel')) return;

    notifPanel = document.createElement('div');
    notifPanel.id = 'push-notif-panel';
    notifPanel.className = 'push-notif-panel';
    notifPanel.setAttribute('aria-label', 'Notification center');
    notifPanel.setAttribute('role', 'dialog');
    notifPanel.innerHTML = [
      '<div class="push-notif-header">',
        '<div class="push-notif-title">',
          '<span class="pn-title-icon">🔔</span>',
          '<span>Notifications</span>',
          '<div id="push-conn-dot" class="push-conn-dot disconnected" title="Disconnected"></div>',
        '</div>',
        '<div class="push-notif-actions">',
          '<button class="pn-btn" id="pn-mark-read" onclick="window.pushNotif.markRead()">Mark read</button>',
          '<button class="pn-btn" id="pn-clear-all" onclick="window.pushNotif.clearAll()">Clear all</button>',
          '<button class="pn-close" onclick="window.pushNotif.closePanel()" aria-label="Close notifications">✕</button>',
        '</div>',
      '</div>',
      '<div class="push-notif-list" id="push-notif-list"></div>',
    ].join('');
    document.body.appendChild(notifPanel);
    renderNotifList();
  }

  function renderNotifList() {
    var listEl = document.getElementById('push-notif-list');
    if (!listEl) return;
    var list = loadNotifications();
    if (!list.length) {
      listEl.innerHTML = '<div class="pn-empty"><div class="pn-empty-icon">🔕</div><div>No notifications yet</div><div class="pn-empty-sub">BOLOs, 911 calls, and alerts will appear here in real time.</div></div>';
      return;
    }
    listEl.innerHTML = list.map(function(n, idx) {
      var ageStr = formatAge(n.ts);
      var unreadClass = n.read ? '' : ' unread';
      return [
        '<div class="notif-item' + unreadClass + ' notif-type-' + n.type + '">',
          '<div class="notif-icon">' + (n.icon || '📢') + '</div>',
          '<div class="notif-body">',
            '<div class="notif-title">' + escSafe(n.title) + '</div>',
            '<div class="notif-detail">' + escSafe(n.detail || '') + '</div>',
            '<div class="notif-age">' + escSafe(ageStr) + '</div>',
          '</div>',
          '<div class="notif-badge-type ' + (n.badgeClass || '') + '">' + escSafe(n.badgeLabel || '') + '</div>',
        '</div>',
      ].join('');
    }).join('');
  }

  function formatAge(ts) {
    if (!ts) return '';
    var diff = Math.floor((Date.now() - ts) / 1000);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  function escSafe(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Panic overlay ────────────────────────────────────────────────────────

  function showPanicOverlay(data) {
    var existing = document.getElementById('panic-overlay');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.id = 'panic-overlay';
    el.className = 'panic-overlay';
    el.innerHTML = [
      '<div class="panic-pulse-ring"></div>',
      '<div class="panic-inner">',
        '<div class="panic-skull">🚨</div>',
        '<div class="panic-title">OFFICER PANIC</div>',
        '<div class="panic-callsign">' + escSafe(data.callsign || 'UNKNOWN') + '</div>',
        '<div class="panic-location">' + escSafe(data.location || 'Location unknown') + '</div>',
        '<div class="panic-call-id">Call ' + escSafe(data.call_id || '') + '</div>',
        '<button class="panic-dismiss" onclick="document.getElementById(\'panic-overlay\').remove()">Acknowledge ✓</button>',
      '</div>',
    ].join('');
    document.body.appendChild(el);
    vibrate([200, 100, 200, 100, 400, 100, 400]);
    setTimeout(function() {
      var ov = document.getElementById('panic-overlay');
      if (ov) ov.remove();
    }, 30000);
  }

  // ── Toast ─────────────────────────────────────────────────────────────────

  function showAlertBanner(opts) {
    var existing = document.querySelectorAll('.push-alert-banner');
    if (existing.length >= 3) { existing[0].remove(); }

    var banner = document.createElement('div');
    banner.className = 'push-alert-banner push-alert-' + (opts.level || 'info');
    banner.innerHTML = [
      '<div class="pab-left">',
        '<span class="pab-icon">' + (opts.icon || '📢') + '</span>',
        '<div class="pab-text">',
          '<div class="pab-title">' + escSafe(opts.title) + '</div>',
          '<div class="pab-detail">' + escSafe(opts.detail || '') + '</div>',
        '</div>',
      '</div>',
      '<button class="pab-close" aria-label="Dismiss">&times;</button>',
    ].join('');
    banner.querySelector('.pab-close').addEventListener('click', function() {
      banner.classList.add('leaving');
      setTimeout(function() { banner.remove(); }, 300);
    });
    document.body.appendChild(banner);
    requestAnimationFrame(function() { banner.classList.add('visible'); });
    var duration = opts.level === 'critical' ? 12000 : 6000;
    setTimeout(function() {
      banner.classList.add('leaving');
      setTimeout(function() { banner.remove(); }, 300);
    }, duration);
  }

  // ── Event handlers ────────────────────────────────────────────────────────

  function onBoloCreated(data) {
    var title = '🚨 BOLO: ' + (data.suspect_name || data.suspectName || 'Unknown');
    var detail = [data.threat_level || data.threatLevel, data.last_location || data.lastLocation, data.vehicle].filter(Boolean).join(' · ');
    addNotification({ type: 'bolo', icon: '🚨', title: title, detail: detail, badgeLabel: data.threat_level || 'BOLO', badgeClass: 'badge-bolo', ts: Date.now() });
    showAlertBanner({ title: title, detail: detail, icon: '🚨', level: (data.threat_level === 'High' || data.threat_level === 'Extreme') ? 'critical' : 'warning' });
    sendBrowserNotif('BOLO Alert', title + ' — ' + detail);
    vibrate([100, 50, 100]);
  }

  function onBoloCleared(data) {
    var title = 'BOLO Cleared: ' + (data.bolo_id || '');
    addNotification({ type: 'bolo-cleared', icon: '✅', title: title, detail: 'BOLO has been cleared.', badgeLabel: 'CLEARED', badgeClass: 'badge-cleared', ts: Date.now() });
    if (window.showToast) window.showToast('BOLO cleared.', 'success');
  }

  function onCallCreated(data) {
    var priority = data.priority || 'Medium';
    var title = '📞 ' + (data.call_type || '911 Call') + ' — ' + (data.location || 'Unknown');
    var detail = (data.caller_name ? 'Caller: ' + data.caller_name + ' · ' : '') + (data.description || '');
    var level = priority === 'Critical' || priority === 'High' ? 'critical' : 'warning';
    addNotification({ type: 'call', icon: '📞', title: title, detail: detail, badgeLabel: priority.toUpperCase(), badgeClass: 'badge-call-' + priority.toLowerCase(), ts: Date.now() });
    showAlertBanner({ title: title, detail: detail, icon: '📞', level: level });
    sendBrowserNotif('Dispatch Call', title + '\n' + detail);
    if (priority === 'Critical' || priority === 'High') vibrate([150, 75, 150, 75, 300]);
    else vibrate([100, 50, 100]);
  }

  function onUnitsAssigned(data) {
    var title = 'Units Assigned — Call ' + (data.call_id || '');
    var detail = 'Units: ' + (Array.isArray(data.units) ? data.units.join(', ') : data.units || '');
    addNotification({ type: 'dispatch', icon: '🚔', title: title, detail: detail, badgeLabel: 'DISPATCH', badgeClass: 'badge-dispatch', ts: Date.now() });
    if (window.showToast) window.showToast(title, 'info');
  }

  function onCallClosed(data) {
    var title = 'Call Closed — ' + (data.call_id || '');
    var detail = data.resolution ? 'Resolution: ' + data.resolution : '';
    addNotification({ type: 'closed', icon: '✅', title: title, detail: detail, badgeLabel: 'CLOSED', badgeClass: 'badge-cleared', ts: Date.now() });
    if (window.showToast) window.showToast('Dispatch call closed.', 'success');
  }

  function onOfficerStatus(data) {
    var title = 'Officer ' + (data.callsign || '—') + ' → ' + (data.status || '—');
    addNotification({ type: 'officer', icon: '👮', title: title, detail: '', badgeLabel: (data.status || '').toUpperCase(), badgeClass: 'badge-officer', ts: Date.now() });
  }

  function onPanic(data) {
    var title = '🚨 OFFICER PANIC — ' + (data.callsign || 'UNKNOWN');
    var detail = 'Location: ' + (data.location || 'Unknown') + ' · Call: ' + (data.call_id || '');
    addNotification({ type: 'panic', icon: '🚨', title: title, detail: detail, badgeLabel: 'PANIC', badgeClass: 'badge-panic', ts: Date.now() });
    showPanicOverlay(data);
    sendBrowserNotif('OFFICER PANIC', data.callsign + ' at ' + data.location);
  }

  function onPresence(data) {
    // silent — don't spam presence events as notifications
  }

  // ── Connection ────────────────────────────────────────────────────────────

  function setConnDot(state) {
    var dot = document.getElementById('push-conn-dot');
    if (!dot) return;
    dot.className = 'push-conn-dot ' + state;
    dot.title = state === 'connected' ? 'Live — connected to server' : state === 'reconnecting' ? 'Reconnecting…' : 'Disconnected';
  }

  function connect() {
    if (typeof io === 'undefined') return;
    try {
      socket = io({ transports: ['websocket', 'polling'], withCredentials: true, reconnectionAttempts: 20, reconnectionDelay: 2000 });
      socket.on('connect', function () {
        connected = true;
        setConnDot('connected');
        var slug = window.GTAVCAD_CONTEXT && window.GTAVCAD_CONTEXT.communitySlug;
        if (slug) { socket.emit('community:join', { community_slug: slug }); }
      });
      socket.on('disconnect', function () {
        connected = false;
        setConnDot('disconnected');
      });
      socket.on('connect_error', function () { setConnDot('reconnecting'); });
      socket.on('reconnecting', function () { setConnDot('reconnecting'); });

      socket.on('bolo:created', onBoloCreated);
      socket.on('bolo:cleared', onBoloCleared);
      socket.on('dispatch:call_created', onCallCreated);
      socket.on('dispatch:units_assigned', onUnitsAssigned);
      socket.on('dispatch:call_closed', onCallClosed);
      socket.on('officer:status_changed', onOfficerStatus);
      socket.on('dispatch:panic', onPanic);
      socket.on('presence:update', onPresence);

      socket.on('socket:ready', function(d) { setConnDot('connected'); });
    } catch (e) {}
  }

  // ── Header bell button injection ──────────────────────────────────────────

  function injectBell() {
    if (document.getElementById('push-bell-btn')) return;
    var header = document.querySelector('.site-header .topbar');
    if (!header) return;

    var bell = document.createElement('button');
    bell.id = 'push-bell-btn';
    bell.className = 'push-bell-btn';
    bell.setAttribute('aria-label', 'Open notifications');
    bell.innerHTML = [
      '🔔',
      '<span id="notif-badge" class="notif-badge" style="display:none;">0</span>',
    ].join('');
    bell.addEventListener('click', function() { window.pushNotif.togglePanel(); });
    header.appendChild(bell);
  }

  // ── Public API ────────────────────────────────────────────────────────────

  fetchNotifications();

  window.pushNotif = {
    togglePanel: function() { panelOpen ? window.pushNotif.closePanel() : window.pushNotif.openPanel(); },
    openPanel: function() {
      buildPanel();
      renderNotifList();
      var panel = document.getElementById('push-notif-panel');
      if (panel) { panel.classList.add('open'); panelOpen = true; markAllRead(); }
    },
    closePanel: function() {
      var panel = document.getElementById('push-notif-panel');
      if (panel) { panel.classList.remove('open'); panelOpen = false; }
    },
    markRead: function() { markAllRead(); },
    clearAll: function() { clearAll(); },
  };

  // ── Boot ──────────────────────────────────────────────────────────────────

  function boot() {
    requestNotifPermission();
    injectBell();
    buildPanel();
    connect();

    // re-join room when community context loads
    window.addEventListener('gtavcad:context-ready', function(e) {
      var slug = e.detail && e.detail.communitySlug;
      if (slug && socket && connected) {
        socket.emit('community:join', { community_slug: slug });
      }
    });

    // close panel on outside click
    document.addEventListener('click', function(e) {
      if (!panelOpen) return;
      var panel = document.getElementById('push-notif-panel');
      var bell = document.getElementById('push-bell-btn');
      if (panel && !panel.contains(e.target) && bell && !bell.contains(e.target)) {
        window.pushNotif.closePanel();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
