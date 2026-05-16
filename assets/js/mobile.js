(function() {
  'use strict';

  var POLICE_ROLES = ['Police', 'LEO', 'Dispatch', 'EMS', 'DOJ', 'Staff', 'Admin', 'Owner'];

  function getCurrentPage() {
    var path = window.location.pathname.toLowerCase();
    var parts = path.split('/').filter(Boolean);
    var leaf = parts[parts.length - 1] || '';
    if (!leaf || leaf === 'index.html' || leaf === '') return 'home';
    if (leaf === 'police.html' || leaf === 'police' || leaf === 'cad.html' || leaf === 'cad') return 'cad';
    if (leaf === 'dmv.html' || leaf === 'dmv') return 'dmv';
    if (leaf === 'businesses.html' || leaf === 'businesses') return 'businesses';
    if (leaf === 'rules.html' || leaf === 'rules') return 'rules';
    if (leaf === 'applications.html' || leaf === 'applications') return 'applications';
    if (leaf === 'donations.html' || leaf === 'donations') return 'donations';
    if (leaf === 'complaints.html' || leaf === 'complaints') return 'complaints';
    if (leaf === 'communities.html' || leaf === 'communities') return 'communities';
    if (leaf === 'community-admin.html' || leaf === 'admin' || leaf==='admin.html') return 'admin';
    if (leaf === 'civilian.html' || leaf==='dispatch') return 'dispatch';
    return 'home';
  }

  function buildCommunityHref(page) {
    var slug = (window.GTAVCAD_CONTEXT && window.GTAVCAD_CONTEXT.communitySlug) || '';
    if (slug) {
      var pageMap = {
        'home': '/c/' + slug + '/',
        'cad': '/c/' + slug + '/cad',
        'dmv': '/c/' + slug + '/dmv.html',
        'businesses': '/c/' + slug + '/businesses.html',
        'rules': '/c/' + slug + '/rules.html',
        'applications': '/c/' + slug + '/applications.html',
        'donations': '/c/' + slug + '/donations.html',
        'complaints': '/c/' + slug + '/complaints.html'
      };
      return pageMap[page] || '/c/' + slug + '/' + page + '.html';
    }
    var pageMap = {
      'home': 'index.html',
      'cad': 'police.html',
      'dmv': 'dmv.html',
      'businesses': 'businesses.html',
      'rules': 'rules.html',
      'applications': 'applications.html',
      'donations': 'donations.html',
      'complaints': 'complaints.html'
    };
    return pageMap[page] || page + '.html';
  }

  function injectMobileNav() {
    if (document.getElementById('mobile-bottom-nav')) return;
    var currentPage = getCurrentPage();

    function tab(id, icon, label, page) {
      var isActive = currentPage === page ? ' active' : '';
      var href = buildCommunityHref(page);
      if (id === 'tab-more') {
        return '<button class="mobile-tab' + isActive + '" id="tab-more" aria-label="More navigation options" onclick="window.mobileMoreMenuOpen()">' +
          '<span class="tab-icon">' + icon + '</span>' + label + '</button>';
      }
      return '<a class="mobile-tab' + isActive + '" href="' + href + '" aria-label="' + label + '">' +
        '<span class="tab-icon">' + icon + '</span>' + label + '</a>';
    }

    var nav = document.createElement('nav');
    nav.id = 'mobile-bottom-nav';
    nav.className = 'mobile-bottom-nav';
    nav.setAttribute('aria-label', 'Mobile bottom navigation');
    nav.innerHTML =
      tab('tab-home', '🏠', 'Home', 'home') +
      tab('tab-cad', '🚓', 'CAD', 'cad') +
      tab('tab-police', '👮', 'Police', 'cad') +
      tab('tab-dmv', '🪪', 'DMV', 'dmv') +
      tab('tab-more', '☰', 'More', 'more');

    document.body.appendChild(nav);
    injectMoreMenu();
  }

  function injectMoreMenu() {
    if (document.getElementById('mobile-more-menu')) return;
    var currentPage = getCurrentPage();

    function menuLink(icon, label, page) {
      var href = buildCommunityHref(page);
      var isActive = currentPage === page ? ' style="color:#fff;border-color:rgba(255,31,31,0.4);"' : '';
      return '<a class="more-menu-link" href="' + href + '"' + isActive + '>' +
        '<span class="link-icon">' + icon + '</span>' +
        '<span class="link-text">' + label + '</span>' +
        '<span class="link-arrow">›</span></a>';
    }

    var logoSrc = '/assets/images/gtavcad-logo.png';
    var menu = document.createElement('div');
    menu.id = 'mobile-more-menu';
    menu.className = 'mobile-more-menu';
    menu.setAttribute('aria-modal', 'true');
    menu.setAttribute('role', 'dialog');
    menu.setAttribute('aria-label', 'More navigation menu');
    menu.innerHTML =
      '<div class="more-menu-header">' +
        '<div class="more-menu-title">' +
          '<img src="' + logoSrc + '" alt="GTAVCAD logo" />' +
          'GTAVCAD' +
        '</div>' +
        '<button class="more-menu-close" onclick="window.mobileMoreMenuClose()" aria-label="Close menu">✕</button>' +
      '</div>' +
      '<div class="more-menu-links">' +
        menuLink('🏠', 'Home', 'home') +
        menuLink('🚔', 'Police / CAD', 'cad') +
        menuLink('🪪', 'DMV', 'dmv') +
        menuLink('🏢', 'Businesses', 'businesses') +
        menuLink('📋', 'Applications', 'applications') +
        menuLink('💬', 'Complaints', 'complaints') +
        menuLink('💰', 'Donations', 'donations') +
        menuLink('🚨', 'Dispatch', 'dispatch') +
        menuLink('🗺️', 'Maps', 'rules') +
        menuLink('🌐', 'Communities', 'communities') +
        menuLink('🛡️', 'Admin', 'admin') +
        menuLink('📖', 'Rules', 'rules') +
        '<a class="more-menu-link" href="https://discord.gg/" target="_blank" rel="noopener noreferrer">' +
          '<span class="link-icon">💬</span>' +
          '<span class="link-text">Join Discord</span>' +
          '<span class="link-arrow">↗</span>' +
        '</a>' +
      (window.__MOBILE_AUTH_LINKS__ || '') + '</div>';

    document.body.appendChild(menu);
  }

  // ── Panic FAB ─────────────────────────────────────────────────────────────

  function injectPanicFab() {
    if (document.getElementById('panic-fab')) return;

    var fab = document.createElement('button');
    fab.id = 'panic-fab';
    fab.className = 'panic-fab';
    fab.setAttribute('aria-label', 'Officer panic button');
    fab.innerHTML = '<span class="panic-fab-icon">🚨</span><span class="panic-fab-label">PANIC</span>';
    fab.style.display = 'none';
    fab.addEventListener('click', openPanicModal);
    document.body.appendChild(fab);

    injectPanicModal();
  }

  function injectPanicModal() {
    if (document.getElementById('panic-modal')) return;
    var modal = document.createElement('div');
    modal.id = 'panic-modal';
    modal.className = 'panic-modal-wrap';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', 'Panic button confirmation');
    modal.innerHTML = [
      '<div class="panic-modal">',
        '<div class="panic-modal-skull">🚨</div>',
        '<h2 class="panic-modal-title">OFFICER PANIC</h2>',
        '<p class="panic-modal-sub">This will alert ALL units immediately.</p>',
        '<div class="panic-modal-fields">',
          '<label class="panic-modal-label">Your Callsign',
            '<input id="panic-callsign" type="text" placeholder="e.g. 1-Adam-12" autocomplete="off" />',
          '</label>',
          '<label class="panic-modal-label">Location',
            '<input id="panic-location" type="text" placeholder="e.g. Forum Drive, Davis" autocomplete="off" />',
          '</label>',
        '</div>',
        '<div id="panic-modal-status" class="panic-modal-status"></div>',
        '<div class="panic-modal-actions">',
          '<button class="panic-modal-cancel" id="panic-modal-cancel" onclick="window.closePanicModal()">Cancel</button>',
          '<button class="panic-modal-confirm" id="panic-modal-confirm" onclick="window.confirmPanic()">',
            '<span id="panic-confirm-text">🚨 ACTIVATE PANIC</span>',
          '</button>',
        '</div>',
      '</div>',
    ].join('');
    document.body.appendChild(modal);

    modal.addEventListener('click', function(e) {
      if (e.target === modal) window.closePanicModal();
    });
  }

  window.openPanicModal = function() {
    var modal = document.getElementById('panic-modal');
    if (modal) {
      modal.classList.add('open');
      document.body.style.overflow = 'hidden';
      var callsignInput = document.getElementById('panic-callsign');
      if (callsignInput) {
        var stored = localStorage.getItem('gtavcad_callsign') || '';
        callsignInput.value = stored;
        setTimeout(function() { callsignInput.focus(); }, 100);
      }
      document.getElementById('panic-modal-status').textContent = '';
      var confirmBtn = document.getElementById('panic-modal-confirm');
      if (confirmBtn) { confirmBtn.disabled = false; }
      document.getElementById('panic-confirm-text').textContent = '🚨 ACTIVATE PANIC';
    }
  };

  window.closePanicModal = function() {
    var modal = document.getElementById('panic-modal');
    if (modal) {
      modal.classList.remove('open');
      document.body.style.overflow = '';
    }
  };

  window.confirmPanic = async function() {
    var callsign = (document.getElementById('panic-callsign').value || '').trim();
    var location = (document.getElementById('panic-location').value || '').trim() || 'Unknown';
    var statusEl = document.getElementById('panic-modal-status');
    var confirmBtn = document.getElementById('panic-modal-confirm');
    var confirmText = document.getElementById('panic-confirm-text');

    if (!callsign) {
      statusEl.textContent = 'Please enter your callsign.';
      statusEl.style.color = '#ff6060';
      document.getElementById('panic-callsign').focus();
      return;
    }

    localStorage.setItem('gtavcad_callsign', callsign);
    confirmBtn.disabled = true;
    confirmText.textContent = 'Sending…';
    statusEl.textContent = '';

    try {
      var slug = (window.GTAVCAD_CONTEXT && window.GTAVCAD_CONTEXT.communitySlug) || '';
      var url = slug ? '/api/dispatch/panic' : '/api/dispatch/panic';
      var res = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callsign: callsign, location: location })
      });
      var data = await res.json().catch(function() { return {}; });
      if (res.ok && data.success) {
        confirmText.textContent = '✓ PANIC ACTIVATED';
        statusEl.textContent = 'All units have been alerted. Stay safe.';
        statusEl.style.color = '#2ecc71';
        if (navigator.vibrate) navigator.vibrate([200, 100, 200, 100, 400]);
        setTimeout(function() { window.closePanicModal(); }, 2500);
      } else {
        statusEl.textContent = data.error || 'Failed to send panic. Check your connection.';
        statusEl.style.color = '#ff6060';
        confirmBtn.disabled = false;
        confirmText.textContent = '🚨 ACTIVATE PANIC';
      }
    } catch (err) {
      statusEl.textContent = 'Network error. Please try again.';
      statusEl.style.color = '#ff6060';
      confirmBtn.disabled = false;
      confirmText.textContent = '🚨 ACTIVATE PANIC';
    }
  };

  function showPanicFab(visible) {
    var fab = document.getElementById('panic-fab');
    if (fab) fab.style.display = visible ? 'flex' : 'none';
  }

  function checkPanicAccess(role, platformRole) {
    if (platformRole === 'owner' || platformRole === 'admin') return true;
    for (var i = 0; i < POLICE_ROLES.length; i++) {
      if (role === POLICE_ROLES[i]) return true;
    }
    return false;
  }

  // ── More menu helpers ──────────────────────────────────────────────────────

  window.mobileMoreMenuOpen = function() {
    var menu = document.getElementById('mobile-more-menu');
    if (menu) {
      menu.classList.add('open');
      document.body.style.overflow = 'hidden';
    }
  };

  window.mobileMoreMenuClose = function() {
    var menu = document.getElementById('mobile-more-menu');
    if (menu) {
      menu.classList.remove('open');
      document.body.style.overflow = '';
    }
  };

  // ── Toast ─────────────────────────────────────────────────────────────────

  window.showToast = function(message, type) {
    type = type || 'info';
    var existing = document.querySelectorAll('.app-toast');
    existing.forEach(function(t) { t.remove(); });
    var toast = document.createElement('div');
    toast.className = 'app-toast ' + type;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function() { toast.classList.add('show'); }, 50);
    setTimeout(function() {
      toast.classList.remove('show');
      setTimeout(function() { toast.remove(); }, 350);
    }, 3200);
  };

  // ── Accordions ────────────────────────────────────────────────────────────

  function initAccordions() {
    document.querySelectorAll('.accordion-trigger').forEach(function(trigger) {
      if (trigger.dataset.accordionBound) return;
      trigger.dataset.accordionBound = 'true';
      var panel = trigger.nextElementSibling;
      if (!panel || !panel.classList.contains('accordion-panel')) return;
      trigger.setAttribute('aria-expanded', 'false');
      trigger.addEventListener('click', function() {
        var expanded = trigger.getAttribute('aria-expanded') === 'true';
        trigger.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        panel.classList.toggle('open', !expanded);
      });
    });
  }

  function closeMoreMenuOnEscape(e) {
    if (e.key === 'Escape') {
      window.mobileMoreMenuClose();
      window.closePanicModal();
    }
  }

  function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function() {
        navigator.serviceWorker.register('/service-worker.js').catch(function() {});
      });
    }
  }

  function updateDiscordLinks() {
    window.addEventListener('gtavcad:context-ready', function(e) {
      var community = e.detail || {};
      var slug = community.communitySlug || '';
      var nav = document.getElementById('mobile-bottom-nav');
      var menu = document.getElementById('mobile-more-menu');
      if (!nav || !menu || !slug) return;
      var tabs = nav.querySelectorAll('.mobile-tab:not(#tab-more)');
      var pages = ['home', 'cad', 'dmv', 'businesses'];
      tabs.forEach(function(tab, i) {
        if (pages[i]) tab.href = buildCommunityHref(pages[i]);
      });
      var links = menu.querySelectorAll('.more-menu-link:not([target])');
      var menuPages = ['home', 'cad', 'dmv', 'businesses', 'applications', 'complaints', 'donations', 'dispatch', 'rules', 'communities', 'admin'];
      links.forEach(function(link, i) {
        if (menuPages[i]) link.href = buildCommunityHref(menuPages[i]);
      });
    });
  }

  // Check session and show panic fab for eligible roles
  function loadSessionAndShowPanic() {
    fetch('/api/auth/session', { credentials: 'include' })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.authenticated && d.user) {
          var role = d.user.role || '';
          var platformRole = d.user.platform_role || '';
          var canPanic = checkPanicAccess(role, platformRole);
          showPanicFab(canPanic);
          // Pre-fill callsign if stored
          if (d.user.username && !localStorage.getItem('gtavcad_callsign')) {
            localStorage.setItem('gtavcad_callsign', d.user.username);
          }
        }
      })
      .catch(function() {});
  }

  
  async function injectTopMobileShell(){
    document.body.classList.add('mobile-only-app');
    var topbar = document.querySelector('.site-header .topbar');
    if (!topbar) return;
    var user='Guest', role='Logged out', community='No community';
    try {
      var r=await fetch('/api/auth/session',{credentials:'include'}); var d=await r.json();
      if(d && d.authenticated && d.user){ user=d.user.username||'User'; role=d.user.role||'Member'; }
    } catch(e){}
    var slug=(window.GTAVCAD_CONTEXT&&window.GTAVCAD_CONTEXT.communitySlug)||'';
    if(slug) community=slug;
    window.__MOBILE_AUTH_LINKS__ = user==='Guest'
      ? '<a class="more-menu-link" href="/login"><span class="link-icon">🔐</span><span class="link-text">Login</span><span class="link-arrow">›</span></a><a class="more-menu-link" href="/register"><span class="link-icon">📝</span><span class="link-text">Register</span><span class="link-arrow">›</span></a>'
      : '<button class="more-menu-link" type="button" data-auth-logout><span class="link-icon">🚪</span><span class="link-text">Logout</span><span class="link-arrow">›</span></button>';
    topbar.innerHTML = '<img class="mobile-shell-logo" src="/assets/images/gtavcad-logo.png" alt="logo"><div class="mobile-shell-meta"><div class="mobile-shell-title">GTAVCAD · '+community+'</div><div class="mobile-shell-sub">'+user+' · '+role+'</div></div><div class="mobile-shell-bell-wrap" id="mobile-shell-bell"></div>';
  }

  function tableToCards(){
    document.querySelectorAll('table').forEach(function(t){ t.classList.add('mobile-table-cards');
      var heads=[...t.querySelectorAll('thead th')].map(function(th){return (th.textContent||'').trim();});
      t.querySelectorAll('tbody tr').forEach(function(tr){ [...tr.children].forEach(function(td,i){ if(!td.getAttribute('data-label')) td.setAttribute('data-label', heads[i]||('Field '+(i+1)));});});
    });
  }

  function init() {
    injectTopMobileShell();
    injectMobileNav();
    tableToCards();
    injectPanicFab();
    initAccordions();
    document.addEventListener('keydown', closeMoreMenuOnEscape);
    registerServiceWorker();
    updateDiscordLinks();
    loadSessionAndShowPanic();

    var observer = new MutationObserver(function() {
      initAccordions();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
